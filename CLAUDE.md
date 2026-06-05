# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI tool that converts a PDF into a single MP3 audiobook. It extracts text, cleans and chunks it, synthesizes each chunk to speech concurrently via Microsoft Edge's online TTS, then merges the chunks into one file.

## Commands

```bash
# Activate the project virtualenv (Python 3.12)
source .venv/bin/activate

# Run the full pipeline on a PDF
python main.py "sample_pdfs/Differential Equations Chatper 8 Summary (3).pdf"
```

There is no test suite, linter, or build step configured. `requirements.txt` exists but is empty — dependencies are installed directly in `.venv`.

### Dependencies

- `pypdf` — PDF text extraction
- `edge-tts` — TTS (requires network access; calls Microsoft Edge's online voices)
- `pydub` — audio concatenation, which **requires `ffmpeg`** installed on the system (`/usr/bin/ffmpeg`)

When adding a dependency, install it into `.venv` and consider populating `requirements.txt`.

## Architecture

The pipeline is orchestrated linearly in `main.py` and split by concern across one-function-per-stage modules:

1. `pdf/extractor.py` → `extract_text(pdf_path)`: concatenates `page.extract_text()` across all pages.
2. `text/processor.py` → `clean_text(text)`: collapses whitespace. `chunk_text(text, max_len=2000)`: splits on sentence boundaries and packs sentences into chunks under `max_len` chars. **Chunking matters** — Edge TTS rejects/struggles with very large single requests, so the chunk size is what enables both reliability and the concurrent generation below.
3. `tts/tts.py` → `generate_audio(chunks)` (async): schedules one `generate_chunk` coroutine per chunk via `asyncio.gather`, throttled by an `asyncio.Semaphore(5)` to cap concurrent network calls. Writes `output/chunks/chunk_NNNN.mp3` and returns the paths in order. Voice is hardcoded to `en-GB-RyanNeural`. Exceptions are collected (`return_exceptions=True`) and printed, not raised — a failed chunk leaves a gap rather than aborting the run.
4. `audio/merge.py` → `merge_audio(chunk_paths, output_path)`: sorts chunk paths (relies on the zero-padded `chunk_NNNN` naming for correct order) and concatenates them. `get_output_path(pdf_path)` derives the intended final path `output/final/{book_name}.mp3`.
5. `text/save.py` → `save_text(path, content)`: writes intermediate text artifacts, creating parent dirs.

`main.py` also accumulates a `run_meta` dict (timings/sizes/paths per stage) intended to be written to `output/runs/{book_name}/meta.json`.

### Output layout (gitignored)

Each conversion is self-contained under a single per-book root, `output/{book_name}/`:

- `output/{book_name}/text/raw.txt` and `text/cleaned.txt` — intermediate text
- `output/{book_name}/chunks/chunk_NNNN.mp3` — per-chunk audio
- `output/{book_name}/final.mp3` — merged audiobook
- `output/{book_name}/meta.json` — run metadata

`book_name` is the PDF filename without extension. `main.py` builds `book_dir` once and derives every path from it; `get_output_path(book_dir)` returns the `final.mp3` path.

### Known rough edges

- `chttp-server/` is an empty placeholder.
- Stale chunks: re-running a shorter book leaves higher-numbered `chunk_NNNN.mp3` files from the previous run in `chunks/`. They are not merged in (merge only consumes the paths generated that run), but they linger on disk.
