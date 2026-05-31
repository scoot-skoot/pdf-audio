# External Libraries
import sys
import asyncio

# Personal Libraries
from pdf.extractor import extract_text
from text.processor import clean_text, chunk_text
from tts.tts import generate_audio
from text.save import save_text



def main(pdf_path: str):
    if len(sys.argv) < 2:
        print("Usage: python main.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    
    text = extract_text(pdf_path)
    save_text("output/text/raw.txt", text)

    cleaned = clean_text(text)
    save_text("output/text/cleaned.text", cleaned)

    chunks = chunk_text(cleaned)

    asyncio.run(generate_audio(chunks))


if __name__ == "__main__":
    main(sys.argv[1])