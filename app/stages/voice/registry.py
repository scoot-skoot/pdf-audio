from typing import TypedDict

from app.stages.text.scene_splitter import Scene

# Deterministic, gender-aware voice assignment (no LLM — "LLM classifies, code
# assigns"). The narrator is fixed; every other speaker gets a voice from the pool
# matching its (confidence-gated) gender, picked unused-first in first-appearance
# order, so the same input always yields the same voice_map and a character keeps
# one voice for the whole book.

NARRATOR_NAME = "Narrator"
NARRATOR_VOICE = "en-GB-RyanNeural"

# Pools below are validated against the live Edge-TTS catalog. The narrator's voice
# is intentionally excluded from the assignable pools so no character sounds like it.
MALE_VOICES = [
    "en-GB-ThomasNeural",
    "en-US-GuyNeural",
    "en-US-ChristopherNeural",
]
FEMALE_VOICES = [
    "en-GB-SoniaNeural",
    "en-GB-LibbyNeural",
    "en-AU-NatashaNeural",
    "en-US-JennyNeural",
    "en-US-AriaNeural",
]
# Neutral fallback for unknown/low-confidence gender: all assignable voices, so
# such characters still get distinct voices rather than collapsing onto one.
UNKNOWN_VOICES = MALE_VOICES + FEMALE_VOICES

# Gender labels weaker than this confidence are treated as unknown.
CONFIDENCE_THRESHOLD = 0.6


class CharacterProfile(TypedDict):
    name: str
    voice: str
    traits: dict


def _effective_gender(meta: dict) -> str:
    gender = (meta or {}).get("gender", "unknown")
    confidence = (meta or {}).get("confidence", 0.0)
    if gender in ("male", "female") and confidence >= CONFIDENCE_THRESHOLD:
        return gender
    return "unknown"


def _pool_for(gender: str) -> list[str]:
    if gender == "male":
        return MALE_VOICES
    if gender == "female":
        return FEMALE_VOICES
    return UNKNOWN_VOICES


def build_voice_map(
    scenes: list[Scene], character_meta: dict[str, dict] = None
) -> dict[str, str]:
    """Assign a stable voice to every speaker by first-appearance order.

    Narrator is always NARRATOR_VOICE. Each other speaker draws from the pool for
    its confidence-gated gender, taking the first unused voice; if a pool is
    exhausted it cycles deterministically by per-pool count. Deterministic: same
    (scene/segment order, character_meta) → same map.
    """
    character_meta = character_meta or {}
    voice_map: dict[str, str] = {NARRATOR_NAME: NARRATOR_VOICE}
    used: set[str] = {NARRATOR_VOICE}
    pool_counts: dict[str, int] = {}

    for scene in scenes:
        for segment in scene.get("segments", []):
            speaker = segment["speaker"]
            if speaker in voice_map:
                continue
            gender = _effective_gender(character_meta.get(speaker))
            pool = _pool_for(gender)
            choice = next((v for v in pool if v not in used), None)
            if choice is None:  # pool exhausted → deterministic cycle
                choice = pool[pool_counts.get(gender, 0) % len(pool)]
            pool_counts[gender] = pool_counts.get(gender, 0) + 1
            used.add(choice)
            voice_map[speaker] = choice
    return voice_map


def build_characters(
    scenes: list[Scene],
    voice_map: dict[str, str],
    character_meta: dict[str, dict] = None,
) -> dict[str, CharacterProfile]:
    """Build a character registry from the voice map, carrying gender/confidence."""
    character_meta = character_meta or {}
    characters: dict[str, CharacterProfile] = {}
    for name, voice in voice_map.items():
        meta = character_meta.get(name, {})
        characters[name] = {
            "name": name,
            "voice": voice,
            "traits": {
                "gender": meta.get("gender", "unknown"),
                "confidence": meta.get("confidence", 0.0),
            },
        }
    return characters
