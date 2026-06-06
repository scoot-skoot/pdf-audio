import re
from typing import TypedDict

from text.scene_splitter import Scene
from voice.registry import NARRATOR_NAME, NARRATOR_VOICE


class Chunk(TypedDict):
    chunk_id: int
    scene_id: int
    text: str
    speaker: str
    voice: str


def chunk_text(text, max_len = 2000):
    sentences = re.split(r'(?<=[.!?]) +', text)

    chunks = []
    current = ""

    for s in sentences:
        if len(current) + len(s) <= max_len:
            current += s + " "
        else:
            chunks.append(current.strip())
            current = s + " "

    if current:
        chunks.append(current.strip())


    return chunks

def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text) # Regular expression cleaning of whitespace
    return text.strip()


def chunk_scenes(
    scenes: list[Scene], voice_map: dict[str, str], max_len: int = 2000
) -> list[Chunk]:
    """Speaker-aware chunking: sentence-pack each speaker segment independently.

    Chunks never span speakers, so each carries a single speaker and voice (from
    voice_map) for multi-voice TTS. chunk_id is a global running index. No LLM.
    """
    chunks: list[Chunk] = []
    chunk_id = 0
    for scene in scenes:
        for segment in scene["segments"]:
            speaker = segment["speaker"]
            voice = voice_map.get(speaker, NARRATOR_VOICE)
            for piece in chunk_text(segment["text"], max_len):
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "scene_id": scene["scene_id"],
                        "text": piece,
                        "speaker": speaker,
                        "voice": voice,
                    }
                )
                chunk_id += 1
    return chunks


def chunk_structured(text: str, max_len: int = 2000) -> list[Chunk]:
    """Structured-mode chunking: chunk_text output wrapped as Narrator Chunks.

    scene_id is 0 (sentinel for "no scene"); speaker/voice are the narrator so the
    Chunk shape is uniform with the narrative branch. No LLM.
    """
    return [
        {
            "chunk_id": i,
            "scene_id": 0,
            "text": piece,
            "speaker": NARRATOR_NAME,
            "voice": NARRATOR_VOICE,
        }
        for i, piece in enumerate(chunk_text(text, max_len))
    ]