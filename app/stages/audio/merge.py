from pydub import AudioSegment
import os

def get_output_path(book_dir: str) -> str:
    return os.path.join(book_dir, "final.mp3")

def merge_audio(chunk_paths: list[str], output_path: str, lead_silences_ms: list[int] = None):
    """Concatenate chunk MP3s in the given order, optionally prepending a silence
    pause before each chunk (lead_silences_ms aligned by index).

    Files are consumed in the order provided (generate_audio already returns final
    order); a missing/unreadable chunk is skipped. Aborts only if no audio exists.
    """
    if not chunk_paths:
        print("[merge] No audio chunks to merge — skipping.")
        return

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    combined = AudioSegment.empty()
    for i, file in enumerate(chunk_paths):
        if not os.path.exists(file):
            continue  # failed chunk → gap, keep going
        if lead_silences_ms and i < len(lead_silences_ms) and lead_silences_ms[i] > 0:
            combined += AudioSegment.silent(duration=lead_silences_ms[i])
        combined += AudioSegment.from_mp3(file)

    if len(combined) == 0:
        print("[merge] No audio produced — skipping export.")
        return

    combined.export(output_path, format="mp3")