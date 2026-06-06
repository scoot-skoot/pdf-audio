import re
from typing import TypedDict

from text.scene_splitter import Scene
from voice.registry import NARRATOR_NAME, NARRATOR_VOICE

# Sentinel scene_ids for non-scene narration (structured main = 0; document
# front/back matter narrated around the main content).
STRUCTURED_ID = 0
FRONT_ID = -1
BACK_ID = -2


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


def chunk_structured(text: str, scene_id: int = STRUCTURED_ID, max_len: int = 2000) -> list[Chunk]:
    """Narrator-only chunking: chunk_text output wrapped as Narrator Chunks.

    Used for structured mode (scene_id=STRUCTURED_ID) and for narrated front/back
    matter (scene_id=FRONT_ID / BACK_ID). speaker/voice are the narrator so the
    Chunk shape is uniform with the narrative branch. No LLM.
    """
    return [
        {
            "chunk_id": i,
            "scene_id": scene_id,
            "text": piece,
            "speaker": NARRATOR_NAME,
            "voice": NARRATOR_VOICE,
        }
        for i, piece in enumerate(chunk_text(text, max_len))
    ]