# External Libraries
import sys
import asyncio
import os

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
    text = extract_text(pdf_path)
    save_text(f"output/text/{book_name}/raw.txt", text)

    cleaned = clean_text(text)
    save_text(f"output/text/{book_name}/cleaned.text", cleaned)

    chunks = chunk_text(cleaned)
    

    chunk_output_dir = f"output/chunks/{book_name}"
    chunk_paths = asyncio.run(generate_audio(chunks))

    output_path = get_output_path(pdf_path)
    merge_audio(chunk_paths, chunk_output_dir)

    


if __name__ == "__main__":
    main(sys.argv[1])