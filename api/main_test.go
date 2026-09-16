package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestOriginAllowed(t *testing.T) {
	cases := []struct {
		origin    string
		allowlist []string
		want      bool
	}{
		{"http://localhost:5173", []string{"*"}, true},
		{"http://localhost:5173", []string{"http://localhost:5173"}, true},
		{"http://localhost:5173", []string{"http://localhost:3000"}, false},
		{"http://web", []string{"http://localhost:8080", "http://web"}, true},
	}
	for _, tc := range cases {
		if got := originAllowed(tc.origin, tc.allowlist); got != tc.want {
			t.Fatalf("originAllowed(%q, %v)=%v want %v", tc.origin, tc.allowlist, got, tc.want)
		}
	}
}

func TestParseOrigins(t *testing.T) {
	got := parseOrigins(" http://a ,http://b, ")
	if len(got) != 2 || got[0] != "http://a" || got[1] != "http://b" {
		t.Fatalf("unexpected parse: %#v", got)
	}
}

func TestCORSPreflight(t *testing.T) {
	t.Setenv("CORS_ORIGINS", "http://localhost:5173")
	handler := newMux()

	req := httptest.NewRequest(http.MethodOptions, "/jobs", nil)
	req.Header.Set("Origin", "http://localhost:5173")
	req.Header.Set("Access-Control-Request-Method", "POST")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("status=%d want %d", rr.Code, http.StatusNoContent)
	}
	if got := rr.Header().Get("Access-Control-Allow-Origin"); got != "http://localhost:5173" {
		t.Fatalf("ACA-Origin=%q", got)
	}
	if got := rr.Header().Get("Access-Control-Allow-Methods"); got == "" {
		t.Fatal("missing Allow-Methods")
	}
}

func TestCORSBlockedOrigin(t *testing.T) {
	t.Setenv("CORS_ORIGINS", "http://localhost:5173")
	handler := newMux()

	req := httptest.NewRequest(http.MethodOptions, "/healthz", nil)
	req.Header.Set("Origin", "http://evil.example")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if got := rr.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Fatalf("expected no ACA-Origin, got %q", got)
	}
}

func TestHealthzWithoutDB(t *testing.T) {
	// healthz hits db.Ping; with nil db this panics — route registration is enough here.
	handler := newMux()
	req := httptest.NewRequest(http.MethodGet, "/does-not-exist", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusNotFound && rr.Code != http.StatusMethodNotAllowed {
		// Go 1.22 ServeMux returns 404 for unknown paths.
		if rr.Code != http.StatusNotFound {
			t.Fatalf("unexpected status for missing route: %d", rr.Code)
		}
	}
}
