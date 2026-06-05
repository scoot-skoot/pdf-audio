from pydub import AudioSegment
import os

def get_output_path(book_dir: str) -> str:
    return os.path.join(book_dir, "final.mp3")

def merge_audio(chunk_paths: list[str], output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    combined = AudioSegment.empty()
    chunk_paths = sorted(chunk_paths)

    for file in chunk_paths:
        combined += AudioSegment.from_mp3(file)

    combined.export(output_path, format="mp3")