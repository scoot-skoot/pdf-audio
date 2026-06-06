import re

from text.processor import Chunk, FRONT_ID, BACK_ID
from voice.registry import NARRATOR_NAME

# Stage 5: deterministic audio conditioning. Annotates each chunk with a pause
# profile and pacing hint, and lightly normalizes text. No LLM, no audio I/O —
# the hints are realized later (rate in TTS, silence in merge).

# pacing_hint → edge-tts `rate` (validated against ^[+-]\d+%$). Conservative.
PACING_RATE = {"slow": "-10%", "normal": "+0%", "fast": "+10%"}

# pause_profile → milliseconds of silence inserted before the chunk at merge:
# narration = paragraph break, dialogue = speaker change, transition = scene change.
PAUSE_MS = {"narration": 100, "dialogue": 200, "transition": 500}

_TERMINAL_PUNCT = ".!?\"')]}…"


class AudioChunk(Chunk):
    pause_profile: str  # "narration" | "dialogue" | "transition"
    pacing_hint: str    # "slow" | "normal" | "fast"


def pacing_to_rate(hint: str) -> str:
    return PACING_RATE.get(hint, "+0%")


def _normalize_text(text: str) -> str:
    """Collapse stray whitespace and ensure a terminal punctuation mark."""
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[-1] not in _TERMINAL_PUNCT:
        text += "."
    return text


def apply_audio_conditioning(chunks: list[Chunk]) -> list[AudioChunk]:
    """Annotate chunks with pause_profile + pacing_hint and normalize text."""
    audio_chunks: list[AudioChunk] = []
    prev_scene = None
    for chunk in chunks:
        scene_id = chunk["scene_id"]
        speaker = chunk["speaker"]

        if prev_scene is not None and scene_id != prev_scene:
            pause_profile = "transition"
        elif speaker != NARRATOR_NAME:
            pause_profile = "dialogue"
        else:
            pause_profile = "narration"

        # Front/back matter is preamble-style narration → slower pacing.
        pacing_hint = "slow" if scene_id in (FRONT_ID, BACK_ID) else "normal"

        audio_chunks.append(
            {
                **chunk,
                "text": _normalize_text(chunk["text"]),
                "pause_profile": pause_profile,
                "pacing_hint": pacing_hint,
            }
        )
        prev_scene = scene_id

    return audio_chunks
