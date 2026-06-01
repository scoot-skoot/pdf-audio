from pydub import AudioSegment
import os

def get_output_path(pdf_path: str) -> str:
    
    book_name, _ = os.path.splitext(os.path.basename(pdf_path)) # Tuple unpacking
    return f"output/final/{book_name}.mp3"

def merge_audio(chunk_paths: list[str], output_path: str):
    combined = AudioSegment.empty()
    chunk_paths = sorted(chunk_paths)

    for file in chunk_paths:
        combined += AudioSegment.from_mp3(file)

    combined.export(output_path, format="mp3")