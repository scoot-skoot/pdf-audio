import re
from typing import TypedDict

from text.scene_splitter import Scene


class Chunk(TypedDict):
    chunk_id: int
    scene_id: int
    text: str


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


def chunk_scenes(scenes: list[Scene], max_len: int = 2000) -> list[Chunk]:
    """Deterministically chunk each scene's text, preserving scene_id.

    Reuses the sentence-aware packing in chunk_text. chunk_id is a global running
    index across all scenes. Never calls the LLM.
    """
    chunks: list[Chunk] = []
    chunk_id = 0
    for scene in scenes:
        for piece in chunk_text(scene["text"], max_len):
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "scene_id": scene["scene_id"],
                    "text": piece,
                }
            )
            chunk_id += 1
    return chunks