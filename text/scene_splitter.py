import asyncio
import json
from dataclasses import dataclass, field
from typing import TypedDict

from llm.client import LLMClient, LLMError


class Segment(TypedDict):
    speaker: str
    text: str
    start_char: int
    end_char: int


class Scene(TypedDict):
    scene_id: int
    text: str
    start_char: int
    end_char: int
    summary: str
    speakers: list[str]   # filled by Call 2 (character_extractor)
    segments: list[Segment]  # filled by Call 2 (character_extractor)


@dataclass
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    coverage: float = 0.0


@dataclass
class SceneResult:
    scenes: list[Scene]
    validation_report: ValidationReport
    fallback_used: bool
    retry_count: int


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
      "summary": "optional short summary"
    }}
  ]
}}
{correction}
TEXT:
{text}
"""

# Appended to the prompt on retries to nudge the model to fix the prior output.
CORRECTION_TEMPLATE = (
    "\nYour previous response was rejected: {reason}. "
    "Return corrected STRICT JSON that satisfies every constraint above.\n"
)


def _single_scene(text: str) -> list[Scene]:
    """Deterministic fallback: the whole text as one scene."""
    return [
        {
            "scene_id": 1,
            "text": text,
            "start_char": 0,
            "end_char": len(text),
            "summary": "",
            "speakers": [],
            "segments": [],
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


def _coverage(output: dict, text_len: int) -> float:
    if text_len <= 0:
        return 0.0
    covered = sum(s["end_char"] - s["start_char"] for s in output["scenes"])
    return covered / text_len


def _parse_offsets_to_scenes(output: dict, clean_text: str) -> list[Scene]:
    """Build Scene objects by slicing clean_text at the validated offsets.

    speakers/segments are left empty here — they are filled by Call 2
    (character_extractor.extract_segments).
    """
    scenes: list[Scene] = []
    for raw in output["scenes"]:
        start = raw["start_char"]
        end = raw["end_char"]
        scenes.append(
            {
                "scene_id": raw["scene_id"],
                "text": clean_text[start:end],
                "start_char": start,
                "end_char": end,
                "summary": raw.get("summary") or "",
                "speakers": [],
                "segments": [],
            }
        )
    return scenes


async def _request_scenes(client: LLMClient, clean_text: str, correction: str = "") -> str:
    user_prompt = USER_PROMPT_TEMPLATE.format(
        text_len=len(clean_text), text=clean_text, correction=correction
    )
    return await client.generate(SYSTEM_PROMPT, user_prompt)


def _fallback_result(clean_text: str, retry_count: int, error: str) -> SceneResult:
    return SceneResult(
        scenes=_single_scene(clean_text),
        validation_report=ValidationReport(ok=False, errors=[error], coverage=0.0),
        fallback_used=True,
        retry_count=retry_count,
    )


def split_into_scenes(clean_text: str) -> SceneResult:
    """Call 1: split cleaned text into scenes via a single LLM call (with retries).

    Always returns a populated SceneResult: on missing API key, repeated invalid
    output, or any network/LLM error, falls back to a single scene covering the
    whole text. This preserves the deterministic-pipeline guarantee. speakers and
    segments are filled later by Call 2 (character_extractor).
    """
    text_len = len(clean_text)
    if text_len == 0:
        return _fallback_result(clean_text, retry_count=0, error="empty text")

    client = LLMClient()
    if not client.api_key:
        print("[scenes] DEEPSEEK_API_KEY not set — falling back to a single scene.")
        return _fallback_result(clean_text, retry_count=0, error="DEEPSEEK_API_KEY not set")

    # On retries, feed the prior failure reason back into the prompt (prompt-
    # correction retry) so the model can fix its output.
    correction = ""
    reason = ""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw = asyncio.run(_request_scenes(client, clean_text, correction))
            output = json.loads(raw)
            if validate_scenes(output, text_len):
                scenes = _parse_offsets_to_scenes(output, clean_text)
                print(f"[scenes] Segmented into {len(scenes)} scene(s) on attempt {attempt}.")
                return SceneResult(
                    scenes=scenes,
                    validation_report=ValidationReport(
                        ok=True, errors=[], coverage=_coverage(output, text_len)
                    ),
                    fallback_used=False,
                    retry_count=attempt - 1,
                )
            reason = "the JSON did not satisfy the scene constraints (ids/offsets/coverage)"
            print(f"[scenes] Attempt {attempt}: output failed validation.")
        except json.JSONDecodeError:
            reason = "the response was not valid JSON"
            print(f"[scenes] Attempt {attempt}: invalid JSON from LLM.")
        except LLMError as e:
            reason = f"the request errored ({e})"
            print(f"[scenes] Attempt {attempt}: LLM error: {e}")

        correction = CORRECTION_TEMPLATE.format(reason=reason)

    print("[scenes] All attempts failed — falling back to a single scene.")
    return _fallback_result(clean_text, retry_count=MAX_RETRIES + 1, error=reason)
