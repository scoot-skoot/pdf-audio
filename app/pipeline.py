# Orchestrates the full PDF -> MP3 pipeline. Importable by cli.py and worker.py.
# The pipeline knows nothing about job/lifecycle states: it only calls on_event(stage)
# with its own internal stage names. Callers (the worker) translate those.
import asyncio
import os
import time
import json

from app.stages.pdf.extractor import extract_text
from app.stages.text.processor import clean_text, chunk_scenes, chunk_structured, FRONT_ID, BACK_ID
from app.stages.text.document import split_document
from app.stages.text.matter_detector import detect_matter
from app.stages.text.scene_splitter import split_into_scenes
from app.stages.text.character_extractor import extract_segments
from app.stages.text.mode_detector import detect_mode
from app.stages.voice.registry import NARRATOR_NAME, NARRATOR_VOICE, build_voice_map, build_characters
from app.stages.tts.tts import generate_audio
from app.stages.text.save import save_text
from app.stages.obs.trace import Trace
from app.stages.audio.conditioning import apply_audio_conditioning, pacing_to_rate, PAUSE_MS
from app.stages.audio.merge import get_output_path, merge_audio

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


def run_pipeline(pdf_path, mode=None, trim_matter=False, book_dir=None, on_event=None):
    """Run the full pipeline end-to-end.

    on_event(stage: str) is called with pipeline-native stage names right before each
    stage runs ("extract", "document_split", "matter_detection", "mode_select",
    "scene_split", "segment_extract", "voice_assign", "chunk", "audio_condition",
    "tts", "merge"). It has no knowledge of DB/lifecycle states. Default: no-op.

    Returns {"output_path", "run_meta", "book_dir"}. Raises on unrecoverable failure.
    """
    emit = on_event or (lambda *_a, **_k: None)

    book_name = os.path.splitext(os.path.basename(pdf_path))[0]
    if book_dir is None:
        book_dir = os.path.join("output", book_name)

    run_meta = {"book": book_name, "timings": {}, "sizes": {}, "paths": {}}
    trace = Trace()

    # Text Extraction
    emit("extract")
    start = time.perf_counter()
    text = extract_text(pdf_path)
    raw_path = os.path.join(book_dir, "text", "raw.txt")
    save_text(raw_path, text)
    run_meta["timings"]["extract"] = time.perf_counter() - start
    run_meta["sizes"]["raw_chars"] = len(text)
    run_meta["paths"]["raw_text"] = raw_path

    print(f"[1] Extracted text length: {len(text)} chars")

    # Stage 0 — Document boundary split (deterministic). Front/back matter are
    # narrated; only main_content runs scene/speaker analysis.
    emit("document_split")
    segs = split_document(text)
    front = clean_text(segs["front_matter"])
    main = clean_text(segs["main_content"])
    back = clean_text(segs["back_matter"])
    run_meta["document"] = {
        "front_chars": len(front), "main_chars": len(main), "back_chars": len(back)
    }
    trace.event("document_split", front=len(front), main=len(main), back=len(back))
    print(f"[2] Document split — front:{len(front)} main:{len(main)} back:{len(back)} chars")

    # Stage 0.5 — optional LLM matter trim (--trim-matter). Refines the deterministic
    # main_content and drops front/back from narration. Default: narrate all three.
    run_meta["trim_matter_enabled"] = trim_matter
    narrate_front, narrate_back = front, back
    if trim_matter:
        emit("matter_detection")
        trace.event("matter_detection_start", chars=len(main))
        start = time.perf_counter()
        matter = detect_matter(main)
        run_meta["timings"]["matter_detection"] = time.perf_counter() - start

        main = matter.main_content
        narrate_front, narrate_back = "", ""

        run_meta["matter"] = {
            "front_chars_removed": len(matter.front_matter),
            "back_chars_removed": len(matter.back_matter),
            "front_confidence": matter.front_confidence,
            "back_confidence": matter.back_confidence,
        }
        run_meta["matter_detection_fallback"] = matter.fallback_used
        run_meta["matter_detection_retries"] = matter.retry_count
        trace.event(
            "matter_detection_fallback" if matter.fallback_used else "matter_detection_success",
            front=len(matter.front_matter), back=len(matter.back_matter),
            front_conf=matter.front_confidence, back_conf=matter.back_confidence,
        )
        print(
            f"[2.5] Matter trim — front:{len(matter.front_matter)} back:{len(matter.back_matter)} "
            f"chars removed (fallback={matter.fallback_used})"
        )

        front_path = os.path.join(book_dir, "text", "front_matter.txt")
        back_path = os.path.join(book_dir, "text", "back_matter.txt")
        save_text(front_path, front + matter.front_matter)
        save_text(back_path, matter.back_matter + back)
        save_text(os.path.join(book_dir, "matter.json"), json.dumps({
            "front_matter": front + matter.front_matter,
            "main_content": matter.main_content,
            "back_matter": matter.back_matter + back,
            "front_confidence": matter.front_confidence,
            "back_confidence": matter.back_confidence,
        }, indent=2))
        run_meta["paths"]["front_matter"] = front_path
        run_meta["paths"]["back_matter"] = back_path

    # Cleaned main content is the canonical text for the pipeline + artifacts.
    cleaned = main
    clean_path = os.path.join(book_dir, "text", "cleaned.txt")
    save_text(clean_path, cleaned)
    run_meta["sizes"]["clean_chars"] = len(cleaned)

    # Mode selection.
    emit("mode_select")
    mode, detected = resolve_mode(mode, cleaned)
    run_meta["mode"] = mode
    if detected is not None:
        run_meta["detected_mode"] = detected
    trace.event("mode_select", mode=mode, detected=detected)
    print(f"[3] Mode: {mode}" + (f" (detected: {detected})" if detected else " (override)"))

    if mode == "narrative":
        # Call 1 — scene boundaries (LLM proposes, code validates + slices).
        emit("scene_split")
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
        emit("segment_extract")
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

        # Deterministic gender-aware voice assignment + character registry (no LLM).
        emit("voice_assign")
        voice_map = build_voice_map(scenes, seg_result.characters)
        characters = build_characters(scenes, voice_map, seg_result.characters)
        run_meta["voice_map"] = voice_map
        run_meta["character_count"] = len(characters)
        trace.event("voice_assign", characters=len(characters))
        print(f"[3.2] Characters: {len(characters)} → voices {list(voice_map.values())}")

        scenes_path = os.path.join(book_dir, "text", "scenes.json")
        save_text(scenes_path, json.dumps(scenes, indent=2))
        save_text(os.path.join(book_dir, "voice_map.json"), json.dumps(voice_map, indent=2))
        save_text(os.path.join(book_dir, "characters.json"), json.dumps(characters, indent=2))
        run_meta["paths"]["scenes"] = scenes_path

        # Speaker-aware chunking.
        emit("chunk")
        start = time.perf_counter()
        chunks = chunk_scenes(scenes, voice_map)
        run_meta["timings"]["chunk"] = time.perf_counter() - start
    else:
        # Structured: deterministic Narrator-only chunking, no LLM.
        emit("chunk")
        voice_map = {NARRATOR_NAME: NARRATOR_VOICE}
        run_meta["voice_map"] = voice_map
        start = time.perf_counter()
        chunks = chunk_structured(cleaned)
        run_meta["timings"]["chunk"] = time.perf_counter() - start

    # Bracket main content with narrated front/back matter, then reindex chunk_id.
    front_chunks = chunk_structured(narrate_front, scene_id=FRONT_ID) if narrate_front else []
    back_chunks = chunk_structured(narrate_back, scene_id=BACK_ID) if narrate_back else []
    chunks = front_chunks + chunks + back_chunks
    for i, c in enumerate(chunks):
        c["chunk_id"] = i

    run_meta["sizes"]["chunks"] = len(chunks)
    run_meta["chunk_count"] = len(chunks)
    trace.event("chunk", chunks=len(chunks), front=len(front_chunks), back=len(back_chunks))
    print(f"[3.3] Chunk count: {len(chunks)} (front {len(front_chunks)}, back {len(back_chunks)})")

    # Stage 5 — Audio conditioning (deterministic): pause/pacing + text normalization.
    emit("audio_condition")
    start = time.perf_counter()
    audio_chunks = apply_audio_conditioning(chunks)
    run_meta["timings"]["audio_condition"] = time.perf_counter() - start
    save_text(os.path.join(book_dir, "chunks.json"), json.dumps(audio_chunks, indent=2))
    profiles = {}
    for c in audio_chunks:
        profiles[c["pause_profile"]] = profiles.get(c["pause_profile"], 0) + 1
    run_meta["pause_profiles"] = profiles
    trace.event("audio_condition", profiles=profiles)
    print(f"[3.4] Audio-conditioned chunks: {profiles}")

    # TTS (Edge TTS, concurrent) — per-chunk voice + pacing rate.
    emit("tts")
    start = time.perf_counter()
    tts_items = [
        {"text": c["text"], "voice": c["voice"], "rate": pacing_to_rate(c["pacing_hint"])}
        for c in audio_chunks
    ]
    chunk_paths = asyncio.run(generate_audio(tts_items, os.path.join(book_dir, "chunks")))
    run_meta["timings"]["tts"] = time.perf_counter() - start
    trace.event("tts", chunks=len(chunk_paths))
    print(f"[4] Audio chunks generated: {len(chunk_paths)}")

    # Merging
    emit("merge")
    start = time.perf_counter()
    output_path = get_output_path(book_dir)
    print(f"[5.1] Merging into {output_path}...")

    lead_silences_ms = [PAUSE_MS[c["pause_profile"]] for c in audio_chunks]
    merge_audio(chunk_paths, output_path, lead_silences_ms)
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

    return {"output_path": output_path, "run_meta": run_meta, "book_dir": book_dir}
