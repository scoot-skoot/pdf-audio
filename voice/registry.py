from typing import TypedDict

from text.scene_splitter import Scene

# Deterministic voice assignment (no LLM). The narrator is fixed; every other
# speaker gets the next voice from a curated Edge-TTS pool in first-appearance
# order, so the same input always yields the same voice_map (invariants C1/C4).

NARRATOR_NAME = "Narrator"
NARRATOR_VOICE = "en-GB-RyanNeural"

# Curated Edge-TTS voices (varied gender/accent), excluding the narrator's.
VOICE_POOL = [
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-AU-NatashaNeural",
    "en-AU-WilliamNeural",
    "en-US-AriaNeural",
    "en-US-ChristopherNeural",
]


class CharacterProfile(TypedDict):
    name: str
    voice: str
    traits: dict


def build_voice_map(scenes: list[Scene]) -> dict[str, str]:
    """Assign a stable voice to every speaker by first-appearance order.

    Narrator is always NARRATOR_VOICE. Other speakers cycle through VOICE_POOL
    (wrapping if there are more speakers than pool entries). Deterministic.
    """
    voice_map: dict[str, str] = {NARRATOR_NAME: NARRATOR_VOICE}
    next_idx = 0
    for scene in scenes:
        for segment in scene.get("segments", []):
            speaker = segment["speaker"]
            if speaker in voice_map:
                continue
            voice_map[speaker] = VOICE_POOL[next_idx % len(VOICE_POOL)]
            next_idx += 1
    return voice_map


def build_characters(
    scenes: list[Scene], voice_map: dict[str, str]
) -> dict[str, CharacterProfile]:
    """Build a character registry from the voice map. traits is a v1 placeholder."""
    characters: dict[str, CharacterProfile] = {}
    for name, voice in voice_map.items():
        characters[name] = {"name": name, "voice": voice, "traits": {}}
    return characters
