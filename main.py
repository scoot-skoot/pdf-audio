# External Libraries
import sys
import asyncio
import os
import time
import json

# Personal Libraries
from pdf.extractor import extract_text
from text.processor import clean_text, chunk_text
from tts.tts import generate_audio
from text.save import save_text
from audio.merge import get_output_path
from audio.merge import merge_audio


def main(pdf_path: str):
    if len(sys.argv) < 2:
        print("Usage: python main.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    book_name = os.path.splitext(os.path.basename(pdf_path))[0]
    book_dir = os.path.join("output", book_name)


    run_meta = {
        "book": book_name,
        "timings": {},
        "sizes": {},
        "paths": {}
    }
    
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

    # Text chunking
    start = time.perf_counter()
    chunks = chunk_text(cleaned)
    run_meta["timings"]["chunk"] = time.perf_counter() - start
    run_meta["sizes"]["chunks"] = len(chunks)

    print(f"[3] Chunk count: {len(chunks)}")
    
    # TTS (Edge_TTS Call /w Concurrency (asycio))
    start = time.perf_counter()
    chunk_paths = asyncio.run(generate_audio(chunks, os.path.join(book_dir, "chunks")))
    run_meta["timings"]["tts"] = time.perf_counter() - start

    print(f"[4] Audio chunks generated: {len(chunk_paths)}")

    # Merging
    start = time.perf_counter()
    output_path = get_output_path(book_dir)
    print(f"[5.1] Merging into {output_path}...")

    merge_audio(chunk_paths, output_path)
    run_meta["timings"]["merge"] = time.perf_counter() - start
    run_meta["paths"]["final"] = output_path

    print(f"[5.2] Final audio: {output_path}")


    # Saving Metadata
    meta_path = os.path.join(book_dir, "meta.json")
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)

    with open(meta_path, "w") as f:
        json.dump(run_meta, f, indent=2)

    print("[6] Run metadata saved.")



if __name__ == "__main__":
    main(sys.argv[1])