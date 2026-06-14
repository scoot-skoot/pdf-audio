# pdf-to-audio

Convert any PDF into a narrated MP3 audiobook. Supports a fast single-voice mode and an LLM-powered multi-voice narrative mode that detects characters and assigns each a distinct voice.

---

## Table of Contents

1. [How it works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Setup](#setup)
4. [Running with Docker (recommended)](#running-with-docker-recommended)
5. [Running locally (without Docker)](#running-locally-without-docker)
6. [Options reference](#options-reference)
7. [Output files](#output-files)
8. [LLM configuration](#llm-configuration)
9. [Troubleshooting](#troubleshooting)

---

## How it works

The pipeline has two modes you choose between with `--mode`:

| Mode | LLM calls | Voices | Best for |
|---|---|---|---|
| `structured` (default) | 0 | Single narrator | Textbooks, papers, technical docs |
| `narrative` | 2 | One per character + narrator | Fiction, dialogue-heavy text |

The optional `--trim-matter` flag adds one extra LLM call to detect and strip front matter (title page, TOC, copyright) and back matter (references, index, appendices) so the audiobook begins near Chapter 1.

Audio is synthesised via **Microsoft Edge TTS** (requires internet). LLM calls go to **DeepSeek** (requires an API key for `narrative` mode and `--trim-matter`; both are free to skip — the tool falls back gracefully).

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

Copy the template and fill in your DeepSeek API key (only needed for `--mode narrative` or `--trim-matter`):

```bash
# The .env file is already in the repo as a blank template
# Edit it directly:
DEEPSEEK_API_KEY=sk-your-key-here
```

Leave `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL` blank to use the defaults (`https://api.deepseek.com` and `deepseek-chat`).

> **Note:** `.env` is gitignored — your key will never be committed.

Get a free DeepSeek API key at [platform.deepseek.com](https://platform.deepseek.com).

---

## Running with Docker (recommended)

### Build the image

```bash
docker compose build
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

## Running locally (without Docker)

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

### 3. Set your API key

```bash
export DEEPSEEK_API_KEY=sk-your-key-here
```

Or add it to a `.env` file and load it with `source .env` / `set -a; source .env; set +a`.

### 4. Run

```bash
# Structured mode
python main.py "path/to/your-book.pdf"

# Narrative mode
python main.py "path/to/your-book.pdf" --mode narrative

# Narrative + trim front/back matter
python main.py "path/to/your-book.pdf" --mode narrative --trim-matter
```

---

## Options reference

| Flag | Values | Default | Description |
|---|---|---|---|
| `pdf_path` | path | *(required)* | Path to the input PDF |
| `--mode` | `structured`, `narrative` | `structured` | Pipeline mode |
| `--trim-matter` | *(boolean flag)* | off | Strip front/back matter via LLM before conversion |

**Mode selection logic:** If `--mode` is omitted (or given an unrecognised value), the tool always runs `structured`. The tool internally detects whether the text looks like fiction, but this only prints a hint — it never automatically switches to `narrative`. You must pass `--mode narrative` explicitly.

---

## Output files

Every run writes to `output/<book-name>/`:

```
output/your-book/
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
Check that the key is actually set in the environment (`echo $DEEPSEEK_API_KEY`). For Docker, ensure it is in `.env` and not accidentally blank. Inspect `output/<book>/trace.json` — the `scene_split` event will show `fallback_used: true` and a reason if the call failed.

**Stale chunks from a previous run**
Re-running on the same PDF leaves higher-numbered `chunk_NNNN.mp3` files from a longer previous run in `chunks/`. They are not included in the new `final.mp3` but do linger on disk. Delete `output/<book>/chunks/` manually before re-running if disk space is a concern.

**Docker volume permissions**
If `output/` files are owned by root after a Docker run, add `user: "${UID}:${GID}"` under the `converter` service in `docker-compose.yml`.
