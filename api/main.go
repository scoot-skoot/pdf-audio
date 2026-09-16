// REST API: creates conversion jobs and returns immediately. The worker does the
// long-running pipeline work. The API only writes QUEUED on creation and reads status;
// it never touches intermediate lifecycle states.
package main

import (
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "github.com/lib/pq"
)

var (
	db         *sql.DB
	uploadsDir = envOr("UPLOADS_DIR", "output/_uploads")
)

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func init() {
	// Resolve once so createJob writes next to the shared output volume regardless of cwd.
	if !filepath.IsAbs(uploadsDir) {
		if abs, err := filepath.Abs(uploadsDir); err == nil {
			uploadsDir = abs
		}
	}
}

type job struct {
	ID             string    `json:"id"`
	Status         string    `json:"status"`
	Mode           *string   `json:"mode"`
	TrimMatter     bool      `json:"trim_matter"`
	ResultLocation *string   `json:"result_location"`
	Error          *string   `json:"error"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

// POST /jobs — multipart: file (PDF, required), mode (optional), trim_matter (optional "true").
func createJob(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(64 << 20); err != nil {
		http.Error(w, "invalid multipart form", http.StatusBadRequest)
		return
	}
	file, hdr, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "missing 'file' field", http.StatusBadRequest)
		return
	}
	defer file.Close()
	if filepath.Ext(strings.ToLower(hdr.Filename)) != ".pdf" {
		http.Error(w, "file must be a .pdf", http.StatusBadRequest)
		return
	}

	var mode any
	if m := r.FormValue("mode"); m != "" {
		mode = m
	}
	trim := r.FormValue("trim_matter") == "true"

	// Insert first to get the id, then save the upload under that id.
	var id string
	if err := db.QueryRow(
		`INSERT INTO jobs (status, pdf_path, mode, trim_matter) VALUES ('QUEUED', '', $1, $2) RETURNING id`,
		mode, trim,
	).Scan(&id); err != nil {
		log.Printf("insert job: %v", err)
		http.Error(w, "could not create job", http.StatusInternalServerError)
		return
	}

	if err := os.MkdirAll(uploadsDir, 0o755); err != nil {
		http.Error(w, "storage error", http.StatusInternalServerError)
		return
	}
	pdfPath := filepath.Join(uploadsDir, id+".pdf")
	out, err := os.Create(pdfPath)
	if err != nil {
		http.Error(w, "storage error", http.StatusInternalServerError)
		return
	}
	if _, err := io.Copy(out, file); err != nil {
		out.Close()
		http.Error(w, "storage error", http.StatusInternalServerError)
		return
	}
	out.Close()

	if _, err := db.Exec(`UPDATE jobs SET pdf_path = $1 WHERE id = $2`, pdfPath, id); err != nil {
		http.Error(w, "could not finalize job", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusCreated, map[string]string{"id": id, "status": "QUEUED"})
}

func getJob(w http.ResponseWriter, r *http.Request) {
	j, err := queryJob(r.PathValue("id"))
	if errors.Is(err, sql.ErrNoRows) {
		http.Error(w, "job not found", http.StatusNotFound)
		return
	}
	if err != nil {
		http.Error(w, "query error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, j)
}

// GET /jobs/{id}/result — serves the merged MP3 once COMPLETED.
func getResult(w http.ResponseWriter, r *http.Request) {
	j, err := queryJob(r.PathValue("id"))
	if errors.Is(err, sql.ErrNoRows) {
		http.Error(w, "job not found", http.StatusNotFound)
		return
	}
	if err != nil {
		http.Error(w, "query error", http.StatusInternalServerError)
		return
	}
	if j.Status != "COMPLETED" || j.ResultLocation == nil {
		http.Error(w, "result not ready (status="+j.Status+")", http.StatusConflict)
		return
	}
	// Local FS now — serve the file. S3 later: http.Redirect to a presigned URL.
	// inline so the web player can stream; download button still uses the download attr.
	w.Header().Set("Content-Type", "audio/mpeg")
	w.Header().Set("Content-Disposition", "inline; filename=\""+j.ID+".mp3\"")
	http.ServeFile(w, r, *j.ResultLocation)
}

func queryJob(id string) (job, error) {
	var j job
	err := db.QueryRow(
		`SELECT id, status, mode, trim_matter, result_location, error, created_at, updated_at
		 FROM jobs WHERE id = $1`, id,
	).Scan(&j.ID, &j.Status, &j.Mode, &j.TrimMatter, &j.ResultLocation, &j.Error, &j.CreatedAt, &j.UpdatedAt)
	return j, err
}

func healthz(w http.ResponseWriter, r *http.Request) {
	if err := db.Ping(); err != nil {
		http.Error(w, "db down", http.StatusServiceUnavailable)
		return
	}
	w.Write([]byte("ok"))
}

// corsMiddleware allows the web UI (and local Vite) to call the API cross-origin.
// CORS_ORIGINS is a comma-separated allowlist; empty or "*" allows any origin.
func corsMiddleware(next http.Handler) http.Handler {
	allowlist := parseOrigins(envOr("CORS_ORIGINS", "*"))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" && originAllowed(origin, allowlist) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Accept")
			w.Header().Set("Access-Control-Expose-Headers", "Content-Disposition, Content-Type, Content-Length")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func parseOrigins(raw string) []string {
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func originAllowed(origin string, allowlist []string) bool {
	for _, allowed := range allowlist {
		if allowed == "*" || allowed == origin {
			return true
		}
	}
	return false
}

func newMux() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /jobs", createJob)
	mux.HandleFunc("GET /jobs/{id}", getJob)
	mux.HandleFunc("GET /jobs/{id}/result", getResult)
	mux.HandleFunc("GET /healthz", healthz)
	return corsMiddleware(mux)
}

func main() {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		log.Fatal("DATABASE_URL is required")
	}
	var err error
	db, err = sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}

	addr := ":" + envOr("PORT", "8080")
	log.Printf("API listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, newMux()))
}
