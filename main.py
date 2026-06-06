# External Libraries
import sys
import argparse
import asyncio
import os
import time
import json

# Personal Libraries
from pdf.extractor import extract_text
from text.processor import clean_text, chunk_scenes, chunk_structured
from text.scene_splitter import split_into_scenes
from text.character_extractor import extract_segments
from text.mode_detector import detect_mode
from voice.registry import NARRATOR_NAME, NARRATOR_VOICE, build_voice_map, build_characters
from tts.tts import generate_audio
from text.save import save_text
from obs.trace import Trace
from audio.merge import get_output_path
from audio.merge import merge_audio

VALID_MODES = {"structured", "narrative"}


def resolve_mode(cli_mode, cleaned):
    """Decide the pipeline mode. A valid CLI override wins. Otherwise auto-detection
    only ever *recommends*: it never triggers the LLM narrative path on its own, so
    the executed mode without an explicit flag is always "structured".

    Returns (mode, detected) where detected is the recommendation when no override
    was given, else None.
    """
    if cli_mode in VALID_MODES:
        return cli_mode, None

    detected = detect_mode(cleaned)
    if detected == "narrative":
        print("[mode] Text looks narrative — re-run with `--mode narrative` to use the LLM path.")
    return "structured", detected


def main(argv):
    parser = argparse.ArgumentParser(description="Convert a PDF into an MP3 audiobook.")
    parser.add_argument("pdf_path", help="Path to the input PDF")
    # No choices=: an invalid value falls back to automatic detection per design.
    parser.add_argument("--mode", default=None, help="Pipeline mode: structured | narrative")
    args = parser.parse_args(argv)

    pdf_path = args.pdf_path
    cli_mode = args.mode

    book_name = os.path.splitext(os.path.basename(pdf_path))[0]
    book_dir = os.path.join("output", book_name)


    run_meta = {
        "book": book_name,
        "timings": {},
        "sizes": {},
        "paths": {}
    }
    trace = Trace()

    # Text Extraction
    start = time.perf_counter()
    text = extract_text(pdf_path)
    raw_path = os.path.join(book_dir, "text", "raw.txt")
    save_text(raw_path, text)
    run_meta["timings"]["extract"] = time.perf_counter() - start
    run_meta["sizes"]["raw_chars"] = len(text)
    run_meta["paths"]["raw_text"] = raw_path

    print(f"[1] Extracted text length: {len(text)} chars")


    # Text Cleaning
    start = time.perf_counter()
    cleaned = clean_text(text)
    run_meta["timings"]["clean"] = time.perf_counter() - start
    run_meta["sizes"]["clean_chars"] = len(cleaned)
    clean_path = os.path.join(book_dir, "text", "cleaned.txt")
    save_text(clean_path, cleaned)
    run_meta["sizes"]["clean_chars"] = len(cleaned)

    print(f"[2] Cleaned text length: {len(cleaned)}")

    # Mode selection: valid CLI override wins; otherwise structured (auto-detection
    # only recommends — it never triggers the LLM path on its own).
    mode, detected = resolve_mode(cli_mode, cleaned)
    run_meta["mode"] = mode
    if detected is not None:
        run_meta["detected_mode"] = detected
    trace.event("mode_select", mode=mode, detected=detected)
    print(f"[3] Mode: {mode}" + (f" (detected: {detected})" if detected else " (override)"))

    if mode == "narrative":
        # Call 1 — scene boundaries (LLM proposes, code validates + slices).
        start = time.perf_counter()
        scene_result = split_into_scenes(cleaned)
        run_meta["timings"]["scene_split"] = time.perf_counter() - start
        scenes = scene_result.scenes
        run_meta["scene_count"] = len(scenes)
        run_meta["scene_split_fallback"] = scene_result.fallback_used
        run_meta["scene_split_retries"] = scene_result.retry_count
        run_meta["scene_coverage"] = scene_result.validation_report.coverage
        trace.event("scene_split", status="fallback" if scene_result.fallback_used else "success",
                    scenes=len(scenes), retries=scene_result.retry_count)
        print(f"[3.1] Scene count: {len(scenes)}")

        # Call 2 — per-scene speaker segments (sees Call 1 scenes; code reconstructs).
        start = time.perf_counter()
        seg_result = extract_segments(scenes, cleaned)
        run_meta["timings"]["segment_extract"] = time.perf_counter() - start
        scenes = seg_result.scenes
        run_meta["segment_count"] = sum(len(s["segments"]) for s in scenes)
        run_meta["segment_extract_fallback"] = seg_result.fallback_used
        run_meta["segment_extract_retries"] = seg_result.retry_count
        run_meta["segment_repairs"] = seg_result.repairs
        trace.event("segment_extract", status="fallback" if seg_result.fallback_used else "success",
                    segments=run_meta["segment_count"], repairs=seg_result.repairs)

        # Deterministic voice assignment + character registry (no LLM).
        voice_map = build_voice_map(scenes)
        characters = build_characters(scenes, voice_map)
        run_meta["voice_map"] = voice_map
        run_meta["character_count"] = len(characters)
        trace.event("voice_assign", characters=len(characters))
        print(f"[3.2] Characters: {len(characters)} → voices {list(voice_map.values())}")

        # Persist narrative artifacts.
        scenes_path = os.path.join(book_dir, "text", "scenes.json")
        save_text(scenes_path, json.dumps(scenes, indent=2))
        save_text(os.path.join(book_dir, "voice_map.json"), json.dumps(voice_map, indent=2))
        save_text(os.path.join(book_dir, "characters.json"), json.dumps(characters, indent=2))
        run_meta["paths"]["scenes"] = scenes_path

        # Speaker-aware chunking.
        start = time.perf_counter()
        chunks = chunk_scenes(scenes, voice_map)
        run_meta["timings"]["chunk"] = time.perf_counter() - start
    else:
        # Structured: deterministic Narrator-only chunking, no LLM.
        voice_map = {NARRATOR_NAME: NARRATOR_VOICE}
        run_meta["voice_map"] = voice_map
        start = time.perf_counter()
        chunks = chunk_structured(cleaned)
        run_meta["timings"]["chunk"] = time.perf_counter() - start

    run_meta["sizes"]["chunks"] = len(chunks)
    run_meta["chunk_count"] = len(chunks)
    trace.event("chunk", chunks=len(chunks))

    print(f"[3.3] Chunk count: {len(chunks)}")

    # TTS (Edge_TTS Call /w Concurrency (asycio)) — per-chunk voice.
    start = time.perf_counter()
    tts_items = [{"text": c["text"], "voice": c["voice"]} for c in chunks]
    chunk_paths = asyncio.run(generate_audio(tts_items, os.path.join(book_dir, "chunks")))
    run_meta["timings"]["tts"] = time.perf_counter() - start
    trace.event("tts", chunks=len(chunk_paths))

    print(f"[4] Audio chunks generated: {len(chunk_paths)}")

    # Merging
    start = time.perf_counter()
    output_path = get_output_path(book_dir)
    print(f"[5.1] Merging into {output_path}...")

    merge_audio(chunk_paths, output_path)
    run_meta["timings"]["merge"] = time.perf_counter() - start
    run_meta["paths"]["final"] = output_path
    trace.event("merge", path=output_path)

    print(f"[5.2] Final audio: {output_path}")


    # Saving Metadata + trace
    trace_path = os.path.join(book_dir, "trace.json")
    trace.save(trace_path)
    run_meta["paths"]["trace"] = trace_path

    meta_path = os.path.join(book_dir, "meta.json")
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)

    with open(meta_path, "w") as f:
        json.dump(run_meta, f, indent=2)

    print("[6] Run metadata + trace saved.")



if __name__ == "__main__":
    main(sys.argv[1:])