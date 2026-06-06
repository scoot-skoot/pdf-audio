# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI tool that converts a PDF into a single MP3 audiobook. It extracts text, cleans it, then runs one of two mutually exclusive pipelines — **structured** (default, deterministic, no LLM, single narrator voice) or **narrative** (two LLM calls: segment into **scenes**, then attribute per-speaker dialogue **segments**, and render each character in its own voice) — chunks the result, synthesizes each chunk to speech concurrently via Microsoft Edge's online TTS, and merges the chunks into one file.

The guiding principle is **LLM decides meaning; code enforces correctness**: the LLM only *proposes* scene boundaries and speaker attributions, and code validates/reconstructs them, assigns voices deterministically, and executes all TTS/file side effects. The pipeline never aborts — any LLM/network/validation failure degrades to a single Narrator-only scene and still produces audio.

## Commands

```bash
# Activate the project virtualenv (Python 3.12)
source .venv/bin/activate

# Run the full pipeline on a PDF (structured mode by default)
python main.py "sample_pdfs/Differential Equations Chatper 8 Summary (3).pdf"

# Force a mode explicitly
python main.py file.pdf --mode structured   # deterministic, no LLM
python main.py file.pdf --mode narrative     # LLM scene segmentation
```

`--mode` accepts `structured` or `narrative`. A valid value always wins. With no flag (or an unrecognized value) the executed mode is **always structured** — `detect_mode` runs only to *recommend* a mode (logged as `detected_mode`, printed as a hint); it never triggers the LLM path on its own. So narrative runs **only** with an explicit `--mode narrative`.

There is no test suite, linter, or build step configured. `requirements.txt` lists the four direct dependencies pinned to the versions in `.venv`.

The narrative path makes **two** DeepSeek (OpenAI-compatible) chat calls per book — scene split, then speaker/segment extraction. Set `DEEPSEEK_API_KEY` to enable it; without it (or on any LLM/network/validation failure) each stage falls back independently (whole book → one scene → one Narrator segment) and still produces audio. Optional overrides: `DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`), `DEEPSEEK_MODEL` (default `deepseek-chat`).

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
3. **Mode selection** (`resolve_mode` in `main.py`): a valid `--mode` override wins; otherwise `text/mode_detector.py` → `detect_mode(text)` runs. `detect_mode` is a deterministic, LLM-free heuristic that scores dialogue density (quotes, dialogue dashes) against technical density (digits, math/code symbols), normalized per ~1000 chars, biased toward `structured`. Its result is only a *recommendation* — the executed mode without an explicit flag is always `structured`. The pipeline then branches into one of the two paths below (4a vs 4b).
4a. **Structured path** — `text/processor.py` → `chunk_structured(text, max_len=2000)`: chunks `clean_text` directly via `chunk_text` and wraps each piece as a `Chunk` with `scene_id=0` and `speaker="Narrator"` / `voice=NARRATOR_VOICE`. No LLM, no scenes.
4b. **Narrative path** — two whole-book LLM calls (never per-scene or per-chunk), then deterministic voice assignment:
   - **Call 1 — `text/scene_splitter.py` → `split_into_scenes(clean_text)`**: returns a `SceneResult` (`scenes`, `ValidationReport`, `fallback_used`, `retry_count`). The LLM returns scene char offsets + `summary` only; text is sliced from `clean_text` at those offsets (no paraphrase risk). `validate_scenes` enforces sequential ids, in-bounds `start < end`, no overlap, ≥`MIN_COVERAGE` (0.9). Retries (`MAX_RETRIES`=2) append a correction note; persistent failure / no key → `_single_scene`. `speakers`/`segments` are left empty here (filled by Call 2).
   - **Call 2 — `text/character_extractor.py` → `extract_segments(scenes, clean_text)`**: receives Call 1's validated scenes and asks the LLM to tile each scene into `Segment`s (`speaker` + scene-relative offsets), `"Narrator"` for narration. `reconstruct_segments` is the **code truth layer**: it clamps offsets to scene bounds, sorts, trims overlaps, fills gaps with Narrator, guarantees full per-scene coverage, slices the text, and normalizes speaker names — so malformed LLM output can't break the tiling. Failure / no key → one Narrator segment per scene. Returns a `SegmentResult` (`fallback_used`, `retry_count`, `repairs`).
   - **`voice/registry.py`** (deterministic, no LLM): `build_voice_map(scenes)` assigns `NARRATOR_VOICE` to Narrator and the next voice from `VOICE_POOL` to each new speaker by first-appearance order (cycles if exceeded), so the same input always yields the same map (invariants: a character always maps to one voice). `build_characters` builds the `CharacterProfile` registry (`traits={}` placeholder).
   - **`text/processor.py` → `chunk_scenes(scenes, voice_map, max_len=2000)`**: sentence-packs each **segment** independently (never spans speakers) so every `Chunk` carries a single `speaker` and its `voice`. Never calls the LLM. **Chunking matters** — Edge TTS struggles with very large single requests, so chunk size enables both reliability and the concurrency below.
5. `llm/client.py` → `LLMClient.generate(system, user)` (async): a thin OpenAI-compatible chat-completions call over `aiohttp` (JSON-mode). Raises `LLMError` on missing key / non-200 / network error; both LLM stages catch this to drive their retry+fallback.
6. `tts/tts.py` → `generate_audio(items)` (async): takes a `list[{"text","voice"}]` (main passes `[{"text":c["text"],"voice":c["voice"]} for c in chunks]`, uniform across both modes), schedules one `generate_chunk` coroutine per item via `asyncio.gather`, throttled by an `asyncio.Semaphore(5)`. Renders each chunk in its own voice (fallback `DEFAULT_VOICE`). Writes `output/{book_name}/chunks/chunk_NNNN.mp3` in order. Exceptions are collected (`return_exceptions=True`) and printed, not raised — a failed chunk leaves a gap rather than aborting.
7. `audio/merge.py` → `merge_audio(chunk_paths, output_path)`: sorts chunk paths (relies on the zero-padded `chunk_NNNN` naming for correct order) and concatenates them. `get_output_path(book_dir)` returns `output/{book_name}/final.mp3`.
8. `text/save.py` → `save_text(path, content)`: writes intermediate text artifacts, creating parent dirs.
9. `obs/trace.py` → `Trace`: an append-only per-run event log (`mode_select`, `scene_split`, `segment_extract`, `voice_assign`, `chunk`, `tts`, `merge`), saved to `output/{book_name}/trace.json`. (Named `obs` to avoid clashing with the stdlib `trace` module.)

`main.py` also accumulates a `run_meta` dict written to `output/{book_name}/meta.json`: `mode`, `detected_mode` (only when no override), `voice_map`, `chunk_count`, and per-stage `timings`/`sizes`/`paths`. In narrative mode it adds `scene_count`, `segment_count`, `character_count`, per-stage `*_fallback`/`*_retries`, `scene_coverage`, and `segment_repairs`.

### Output layout (gitignored)

Each conversion is self-contained under a single per-book root, `output/{book_name}/`:

- `output/{book_name}/text/raw.txt` and `text/cleaned.txt` — intermediate text
- `output/{book_name}/text/scenes.json` — scenes with reconstructed speaker segments (narrative only)
- `output/{book_name}/voice_map.json` and `characters.json` — speaker→voice map + character registry (narrative only)
- `output/{book_name}/chunks/chunk_NNNN.mp3` — per-chunk audio
- `output/{book_name}/final.mp3` — merged audiobook
- `output/{book_name}/meta.json` — run metadata
- `output/{book_name}/trace.json` — append-only per-stage event log

`book_name` is the PDF filename without extension. `main.py` builds `book_dir` once and derives every path from it; `get_output_path(book_dir)` returns the `final.mp3` path.

### Known rough edges

- `chttp-server/` is an empty placeholder.
- Stale chunks: re-running a shorter book leaves higher-numbered `chunk_NNNN.mp3` files from the previous run in `chunks/`. They are not merged in (merge only consumes the paths generated that run), but they linger on disk.
