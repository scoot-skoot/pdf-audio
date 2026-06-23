# pdf-to-audio

Convert any PDF into a narrated MP3 audiobook. Supports a fast single-voice mode and an LLM-powered multi-voice narrative mode that detects characters and gives each a distinct voice.

Use it two ways:

- **CLI** — convert a PDF on your machine in one command.
- **REST API + worker** — submit jobs over HTTP; a background worker runs the conversion and you poll for the result. (Designed for eventual deployment: API → AWS ECS, Postgres → RDS, audio → S3.)

Both run the **same pipeline code** — the CLI and the worker just call it differently.

---

## Table of Contents

1. [How it works](#how-it-works) ⚙️
2. [The pipeline, stage by stage](#the-pipeline-stage-by-stage) 🔬
3. [Prerequisites](#prerequisites) 🛑
4. [Setup](#setup)
5. [Running the CLI with Docker](#running-the-cli-with-docker)
6. [Running the CLI locally](#running-the-cli-locally)
7. [Running the REST API](#running-the-rest-api) 🌐
8. [Options reference](#options-reference) 🤓 Textbook | 🧙 Narrative
9. [Output files](#output-files)
10. [LLM configuration](#llm-configuration)
11. [Troubleshooting](#troubleshooting)

---

## How it works

You pick between two modes with `--mode`:

| Mode | LLM calls | Voices | Best for |
|---|---|---|---|
| `structured` (default) | 0 | Single narrator | Textbooks, papers, technical docs |
| `narrative` | 2 | One per character + narrator | Fiction, dialogue-heavy text |

The optional `--trim-matter` flag adds one extra LLM call that detects and strips front matter (title page, TOC, copyright) and back matter (references, index, appendices) so the audiobook begins near Chapter 1.

Audio is synthesised via **Microsoft Edge TTS** (requires internet). LLM calls go to **DeepSeek** (needed only for `narrative` mode and `--trim-matter`; both are safe to skip — the tool falls back gracefully and still produces an MP3).

**Guiding principle — _the LLM decides meaning; code enforces correctness._** The LLM only *proposes* scene boundaries and who's speaking. Code then validates those proposals, slices the original text at the proposed offsets (so the LLM can never paraphrase or hallucinate words into the audio), assigns voices deterministically, and does all the file/audio work. If the LLM is unavailable or returns garbage, each stage independently falls back to a simpler result — the pipeline never aborts.

---

## The pipeline, stage by stage

Here's what actually happens to your text, top to bottom. The names in `CODE` are the modules under `app/stages/`.

```
PDF
 │
 ▼  ① EXTRACT      pdf/extractor.py      — pull raw text out of every page
 │
 ▼  ② CLEAN        text/processor.py     — collapse whitespace, split off front/back matter
 │
 ▼  ②·⑤ TRIM       text/matter_detector  — (only with --trim-matter) LLM finds where the real
 │                                          body starts/ends; code anchors it to real words
 │
 ▼  ③ PICK MODE    text/mode_detector    — structured (default) or narrative
 │
 ├─ structured ───────────────────────────────────────────────┐
 │                                                             │
 │  narrative                                                  │
 ▼  ④a SCENE SPLIT  text/scene_splitter   — LLM proposes scene boundaries (offsets only);
 │                                          code validates coverage, slices the text
 ▼  ④b SEGMENTS     text/character_extractor — LLM tiles each scene into per-speaker segments;
 │                                          code clamps/sorts/fills gaps → guaranteed full coverage
 ▼  ④c VOICES       voice/registry        — deterministically map each character → a fixed voice
 │                                                             │
 ▼  ⑤ CHUNK         text/processor        — split into ≤2000-char chunks, each tagged with a voice ◄┘
 │
 ▼  ⑥ CONDITION    audio/conditioning    — pause/pacing hints + text normalization per chunk
 │
 ▼  ⑦ SYNTHESISE   tts/tts.py            — Edge TTS renders each chunk to MP3 (up to 5 in parallel)
 │
 ▼  ⑧ MERGE        audio/merge.py        — concatenate chunks (in order) into one final.mp3
 │
final.mp3  (+ meta.json, trace.json, and intermediate text for transparency)
```

The whole thing is orchestrated in `app/pipeline.py` → `run_pipeline(...)`, which is importable: the CLI (`cli.py`) and the worker (`worker.py`) both just call it.

In `structured` mode steps ④a–④c are skipped entirely — there's no LLM, every chunk is the narrator, and the result is fully deterministic.

---

## Prerequisites

### Docker path (recommended)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine + Compose plugin

### Local path
- Python 3.12+
- `ffmpeg` installed on your system (`brew install ffmpeg` / `apt install ffmpeg`)
- Internet access (Edge TTS calls Microsoft's servers)

---

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd pdf-to-audio
```

### 2. Configure environment variables

Edit `.env` (shipped as a blank template) and add your DeepSeek API key — only needed for `--mode narrative` or `--trim-matter`:

```bash
DEEPSEEK_API_KEY=sk-your-key-here
```

Leave `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL` blank to use the defaults (`https://api.deepseek.com` and `deepseek-chat`).

> **Note:** `.env` is gitignored — your key will never be committed.

Get a free DeepSeek API key at [platform.deepseek.com](https://platform.deepseek.com).

---

## Running the CLI with Docker

### Build the image

```bash
docker compose build converter
```

### Convert a PDF

Drop your PDF into the `input/` folder, then run:

```bash
# Structured mode (default, no API key needed)
docker compose run --rm converter /app/input/your-book.pdf

# Narrative mode (multi-voice, requires DEEPSEEK_API_KEY)
docker compose run --rm converter /app/input/your-book.pdf --mode narrative

# Narrative mode + strip front/back matter
docker compose run --rm converter /app/input/your-book.pdf --mode narrative --trim-matter
```

The finished audiobook appears at `output/your-book/final.mp3` on your host machine.

---

## Running the CLI locally

### 1. Create and activate a virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\activate           # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your API key (optional — only for narrative / --trim-matter)

```bash
export DEEPSEEK_API_KEY=sk-your-key-here
```

Or add it to `.env` and load it with `set -a; source .env; set +a`.

### 4. Run

```bash
# Structured mode
python cli.py "path/to/your-book.pdf"

# Narrative mode
python cli.py "path/to/your-book.pdf" --mode narrative

# Narrative + trim front/back matter
python cli.py "path/to/your-book.pdf" --mode narrative --trim-matter
```

---

## Running the REST API

The API is the public interface: you `POST` a PDF, get a job ID back **immediately**, and a background worker does the (slow) conversion. You poll for status and download the result when it's ready. Job state lives in Postgres; the API itself never blocks on a conversion.

### Start the stack

```bash
docker compose up --build postgres api worker
```

This starts three services: **postgres** (job state), **api** (Go, on `localhost:8080`), and **worker** (Python, runs the pipeline). Scale workers with `--scale worker=2` — jobs are claimed with `FOR UPDATE SKIP LOCKED`, so no job is ever processed twice.

### Endpoints

| Method & path | Description |
|---|---|
| `POST /jobs` | Create a job. Multipart form: `file` (PDF, required), `mode` (optional), `trim_matter=true` (optional). Returns `201 {id, status:"QUEUED"}` right away. |
| `GET /jobs/{id}` | Job status + metadata (`status`, `error`, `result_location`, timestamps). |
| `GET /jobs/{id}/result` | Download `final.mp3` once the job is `COMPLETED` (otherwise `409`). |
| `GET /healthz` | Liveness + DB ping. |

### Example

```bash
# 1. Submit a job
curl -F "file=@sample_pdfs/ladyWithDog.pdf" -F mode=narrative http://localhost:8080/jobs
# → {"id":"e5ef16c8-...","status":"QUEUED"}

# 2. Poll until COMPLETED
curl http://localhost:8080/jobs/e5ef16c8-...
# → {"status":"GENERATING_AUDIO", ...}

# 3. Download the audiobook
curl -OJ http://localhost:8080/jobs/e5ef16c8-.../result
```

### Job lifecycle

A job moves through these states (the worker translates internal pipeline stages into them):

```
QUEUED → EXTRACTING → CHUNKING → GENERATING_AUDIO → MERGING → COMPLETED
                                                              ↘ FAILED  (any unrecoverable error)
```

Because the pipeline degrades gracefully on LLM/network hiccups, `FAILED` is reserved for genuinely unrecoverable problems (corrupt PDF, disk, DB).

---

## Options reference

| Flag | Values | Default | Description |
|---|---|---|---|
| `pdf_path` | path | *(required)* | Path to the input PDF |
| `--mode` | `structured`, `narrative` | `structured` | Pipeline mode |
| `--trim-matter` | *(boolean flag)* | off | Strip front/back matter via LLM before conversion |

**Mode selection logic:** If `--mode` is omitted (or given an unrecognised value), the tool always runs `structured`. It internally detects whether the text looks like fiction, but this only prints a hint — it never auto-switches to `narrative`. You must pass `--mode narrative` explicitly. (The API exposes the same `mode` / `trim_matter` as form fields.)

---

## Output files

Each conversion writes everything under one folder:

- **CLI runs:** `output/<book-name>/` (book name = the PDF filename)
- **API/worker runs:** `output/jobs/<job-id>/` (uploads land in `output/_uploads/<job-id>.pdf`)

```
output/<book-name>/
├── final.mp3               ← the finished audiobook
├── chunks/
│   ├── chunk_0000.mp3
│   ├── chunk_0001.mp3
│   └── ...
├── text/
│   ├── raw.txt             ← text as extracted from the PDF
│   ├── cleaned.txt         ← text fed into the pipeline
│   ├── front_matter.txt    ← trimmed front matter (--trim-matter only)
│   ├── back_matter.txt     ← trimmed back matter (--trim-matter only)
│   └── scenes.json         ← scene/segment breakdown (narrative only)
├── voice_map.json          ← character → voice assignments (narrative only)
├── characters.json         ← character registry (narrative only)
├── matter.json             ← trim detection result (--trim-matter only)
├── meta.json               ← run metadata (timings, sizes, config)
└── trace.json              ← per-stage event log
```

The intermediate text and JSON are kept on purpose — they're how you inspect what the pipeline (and the LLM) actually did.

---

## LLM configuration

All three LLM env vars are optional unless you use `--mode narrative` or `--trim-matter`:

| Variable | Default | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | *(none)* | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Override to use any OpenAI-compatible API |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name |

Because `DEEPSEEK_BASE_URL` accepts any OpenAI-compatible endpoint, you can point the tool at **OpenAI, Ollama, or any local model** by changing those two variables — no code changes needed.

**Fallback behaviour:** If the API key is missing or any LLM call fails, each stage falls back independently (whole book → one scene → Narrator-only voice; matter trim → no trim). The pipeline never aborts — you always get an MP3.

---

## Troubleshooting

**`ffmpeg` not found**
The Docker image includes `ffmpeg`. For local runs: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Ubuntu/Debian).

**TTS step is slow or times out**
Edge TTS calls Microsoft's servers. Up to 5 chunks are synthesised concurrently. A slow network will slow this step. There is no offline fallback.

**LLM falls back to single Narrator voice even though I set `DEEPSEEK_API_KEY`**
Check that the key is actually set (`echo $DEEPSEEK_API_KEY`). For Docker, ensure it's in `.env` and not blank. Inspect `output/<book>/trace.json` — the `scene_split` event shows `status: "fallback"` and a reason if the call failed.

**API job stuck in `QUEUED`**
The worker isn't picking it up. Check `docker compose logs worker` — it must reach the same Postgres as the API and (for narrative jobs) have `DEEPSEEK_API_KEY` in its environment.

**Stale chunks from a previous run**
Re-running on the same PDF leaves higher-numbered `chunk_NNNN.mp3` files from a longer previous run in `chunks/`. They are not included in the new `final.mp3` but do linger on disk. Delete `output/<book>/chunks/` before re-running if disk space is a concern.

**Docker volume permissions**
If `output/` files are owned by root after a Docker run, add `user: "${UID}:${GID}"` under the relevant service in `docker-compose.yml`.
