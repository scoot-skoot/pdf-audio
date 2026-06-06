import asyncio
import json
from typing import TypedDict

from llm.client import LLMClient, LLMError


class Scene(TypedDict):
    scene_id: int
    text: str
    start_char: int
    end_char: int
    summary: str
    characters: list[str]


# Number of LLM retries after the first attempt (total attempts = MAX_RETRIES + 1).
MAX_RETRIES = 2
# Minimum fraction of the text that must be covered by scenes for the output to
# be considered valid. Gaps are allowed, but degenerate output is rejected.
MIN_COVERAGE = 0.9


SYSTEM_PROMPT = (
    "You are a narrative scene segmentation engine. You split a book's text into "
    "structural scenes and return STRICT JSON ONLY, with no prose, no markdown, and "
    "no code fences."
)

USER_PROMPT_TEMPLATE = """Segment the following text into scenes.

Rules:
- Segment ONLY on: location shifts, time shifts, or major character entry/exit.
- Do NOT over-segment on dialogue micro-breaks.
- Prefer fewer, stable scenes.

The text has exactly {text_len} characters (0-indexed). Report each scene by its
character offsets into this exact text. Offsets must satisfy:
- 0 <= start_char < end_char <= {text_len}
- scenes are ordered and non-overlapping (scene[i].end_char <= scene[i+1].start_char)
- scene_id is sequential starting at 1
- scenes should cover essentially all of the text

Return STRICT JSON ONLY in exactly this shape:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "start_char": 0,
      "end_char": 1200,
      "summary": "optional short summary",
      "characters": ["optional", "list"]
    }}
  ]
}}

TEXT:
{text}
"""


def _single_scene(text: str) -> list[Scene]:
    """Deterministic fallback: the whole text as one scene."""
    return [
        {
            "scene_id": 1,
            "text": text,
            "start_char": 0,
            "end_char": len(text),
            "summary": "",
            "characters": [],
        }
    ]


def validate_scenes(output: dict, text_len: int) -> bool:
    """Enforce structural guarantees on raw LLM output (offsets only).

    Checks: non-empty scene list, sequential scene_ids from 1, in-bounds offsets
    with start < end, no overlaps (gaps allowed), and total coverage >= MIN_COVERAGE.
    """
    if not isinstance(output, dict):
        return False
    scenes = output.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return False

    covered = 0
    prev_end = 0
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            return False
        if scene.get("scene_id") != i + 1:
            return False

        start = scene.get("start_char")
        end = scene.get("end_char")
        if not isinstance(start, int) or not isinstance(end, int):
            return False
        if not (0 <= start < end <= text_len):
            return False
        if start < prev_end:  # overlap with previous scene
            return False

        covered += end - start
        prev_end = end

    if text_len > 0 and covered / text_len < MIN_COVERAGE:
        return False

    return True


def _parse_offsets_to_scenes(output: dict, clean_text: str) -> list[Scene]:
    """Build Scene objects by slicing clean_text at the validated offsets."""
    scenes: list[Scene] = []
    for raw in output["scenes"]:
        start = raw["start_char"]
        end = raw["end_char"]
        characters = raw.get("characters") or []
        if not isinstance(characters, list):
            characters = []
        scenes.append(
            {
                "scene_id": raw["scene_id"],
                "text": clean_text[start:end],
                "start_char": start,
                "end_char": end,
                "summary": raw.get("summary") or "",
                "characters": characters,
            }
        )
    return scenes


async def _request_scenes(client: LLMClient, clean_text: str) -> str:
    user_prompt = USER_PROMPT_TEMPLATE.format(text_len=len(clean_text), text=clean_text)
    return await client.generate(SYSTEM_PROMPT, user_prompt)


def split_into_scenes(clean_text: str) -> list[Scene]:
    """Split cleaned text into scenes via a single LLM call (with retries).

    Always returns a usable list of scenes: on missing API key, repeated invalid
    output, or any network/LLM error, falls back to a single scene covering the
    whole text. This preserves the deterministic-pipeline guarantee.
    """
    text_len = len(clean_text)
    if text_len == 0:
        return _single_scene(clean_text)

    client = LLMClient()
    if not client.api_key:
        print("[scenes] DEEPSEEK_API_KEY not set — falling back to a single scene.")
        return _single_scene(clean_text)

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw = asyncio.run(_request_scenes(client, clean_text))
            output = json.loads(raw)
            if validate_scenes(output, text_len):
                scenes = _parse_offsets_to_scenes(output, clean_text)
                print(f"[scenes] Segmented into {len(scenes)} scene(s) on attempt {attempt}.")
                return scenes
            print(f"[scenes] Attempt {attempt}: output failed validation.")
        except json.JSONDecodeError:
            print(f"[scenes] Attempt {attempt}: invalid JSON from LLM.")
        except LLMError as e:
            print(f"[scenes] Attempt {attempt}: LLM error: {e}")

    print("[scenes] All attempts failed — falling back to a single scene.")
    return _single_scene(clean_text)
