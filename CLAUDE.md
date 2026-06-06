# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI tool that converts a PDF into a single MP3 audiobook. It extracts text, cleans it, segments it into structural **scenes** via a single LLM call, deterministically chunks each scene, synthesizes each chunk to speech concurrently via Microsoft Edge's online TTS, then merges the chunks into one file. The scene layer is structural metadata only — chunks remain the unit fed to TTS.

## Commands

```bash
# Activate the project virtualenv (Python 3.12)
source .venv/bin/activate

# Run the full pipeline on a PDF
python main.py "sample_pdfs/Differential Equations Chatper 8 Summary (3).pdf"
```

There is no test suite, linter, or build step configured. `requirements.txt` exists but is empty — dependencies are installed directly in `.venv`.

The scene-segmentation stage calls DeepSeek's (OpenAI-compatible) chat API. Set `DEEPSEEK_API_KEY` in the environment to enable it; without it (or on any LLM/network/validation failure) the pipeline falls back to treating the whole book as one scene and still produces audio. Optional overrides: `DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`), `DEEPSEEK_MODEL` (default `deepseek-chat`).

### Dependencies

- `pypdf` — PDF text extraction
- `edge-tts` — TTS (requires network access; calls Microsoft Edge's online voices)
- `pydub` — audio concatenation, which **requires `ffmpeg`** installed on the system (`/usr/bin/ffmpeg`)
- `aiohttp` — already present (transitively via `edge-tts`); the LLM client reuses it rather than adding a new HTTP dependency

When adding a dependency, install it into `.venv` and consider populating `requirements.txt`.

## Architecture

The pipeline is orchestrated linearly in `main.py` and split by concern across one-function-per-stage modules:

1. `pdf/extractor.py` → `extract_text(pdf_path)`: concatenates `page.extract_text()` across all pages.
2. `text/processor.py` → `clean_text(text)`: collapses whitespace.
3. `text/scene_splitter.py` → `split_into_scenes(clean_text)`: segments the cleaned text into `Scene` objects (`scene_id`, `text`, `start_char`, `end_char`, `summary`, `characters`) via **exactly one** LLM call per book. The LLM returns char offsets only; the text is sliced from `clean_text` at those offsets (no paraphrase risk). `validate_scenes(output, text_len)` enforces sequential ids, in-bounds `start < end`, no overlap, and ≥`MIN_COVERAGE` (0.9) coverage. On invalid JSON/validation it retries (`MAX_RETRIES`=2); on missing key or any persistent failure it falls back to `_single_scene` (whole text as scene 1). **Constraint: one LLM call per book — never per-scene or per-chunk.** `characters` is a placeholder for a future voice-assignment system and is unused for now.
4. `text/processor.py` → `chunk_scenes(scenes, max_len=2000)`: for each scene, reuses `chunk_text` (splits on sentence boundaries, packs sentences under `max_len` chars) and wraps each piece as a `Chunk` (`chunk_id` running global, `scene_id` preserved, `text`). Never calls the LLM. **Chunking matters** — Edge TTS rejects/struggles with very large single requests, so the chunk size is what enables both reliability and the concurrent generation below.
5. `llm/client.py` → `LLMClient.generate(system, user)` (async): a thin OpenAI-compatible chat-completions call over `aiohttp` (JSON-mode response). Raises `LLMError` on missing key / non-200 / network error; the scene splitter catches this to drive its retry+fallback.
6. `tts/tts.py` → `generate_audio(chunks)` (async): takes a `list[str]` (main passes `[c["text"] for c in chunks]`), schedules one `generate_chunk` coroutine per chunk via `asyncio.gather`, throttled by an `asyncio.Semaphore(5)` to cap concurrent network calls. Writes `output/{book_name}/chunks/chunk_NNNN.mp3` and returns the paths in order. Voice is hardcoded to `en-GB-RyanNeural`. Exceptions are collected (`return_exceptions=True`) and printed, not raised — a failed chunk leaves a gap rather than aborting the run.
7. `audio/merge.py` → `merge_audio(chunk_paths, output_path)`: sorts chunk paths (relies on the zero-padded `chunk_NNNN` naming for correct order) and concatenates them. `get_output_path(book_dir)` returns `output/{book_name}/final.mp3`.
8. `text/save.py` → `save_text(path, content)`: writes intermediate text artifacts, creating parent dirs.

`main.py` also accumulates a `run_meta` dict (timings/sizes/paths per stage, plus `scenes` count and `scene_sizes`) written to `output/{book_name}/meta.json`.

### Output layout (gitignored)

Each conversion is self-contained under a single per-book root, `output/{book_name}/`:

- `output/{book_name}/text/raw.txt` and `text/cleaned.txt` — intermediate text
- `output/{book_name}/text/scenes.json` — segmented scene objects (debugging artifact)
- `output/{book_name}/chunks/chunk_NNNN.mp3` — per-chunk audio
- `output/{book_name}/final.mp3` — merged audiobook
- `output/{book_name}/meta.json` — run metadata

`book_name` is the PDF filename without extension. `main.py` builds `book_dir` once and derives every path from it; `get_output_path(book_dir)` returns the `final.mp3` path.

### Known rough edges

- `chttp-server/` is an empty placeholder.
- Stale chunks: re-running a shorter book leaves higher-numbered `chunk_NNNN.mp3` files from the previous run in `chunks/`. They are not merged in (merge only consumes the paths generated that run), but they linger on disk.
