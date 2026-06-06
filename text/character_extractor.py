import asyncio
import json
from dataclasses import dataclass

from llm.client import LLMClient, LLMError
from text.scene_splitter import Scene, Segment

# Call 2: given the validated scenes from Call 1, attribute every span of each
# scene to a speaker ("Narrator" for narration). The LLM proposes segments; code
# reconstructs them into a gap-free, non-overlapping tiling of each scene (the
# truth layer). One whole-book call; no per-scene or per-chunk calls.

NARRATOR = "Narrator"
MAX_RETRIES = 2

SYSTEM_PROMPT = (
    "You are a dialogue attribution engine. For each scene you label every span of "
    "text with the speaker of its dialogue, or \"Narrator\" for narration. You return "
    "STRICT JSON ONLY — no prose, no markdown, no code fences."
)

USER_PROMPT_TEMPLATE = """Attribute speakers for each scene below, and classify each character.

For every scene, return a list of `segments` that tile the WHOLE scene with no gaps
and no overlaps. Each segment has:
- "speaker": the character speaking, or "Narrator" for narration/description
- "start_char" and "end_char": offsets RELATIVE TO THAT SCENE'S TEXT (0-indexed),
  where 0 <= start_char < end_char <= the scene length shown below.

Also return a `characters` list: one entry per distinct non-Narrator speaker, with:
- "character": the exact name used as a speaker above
- "gender": "male", "female", or "unknown"
- "confidence": a number from 0 to 1 for how sure you are of the gender

Rules:
- Use "Narrator" for everything that is not a character's spoken dialogue.
- Keep speaker names consistent across scenes (same character → same exact name).
- Segments within a scene must be ordered and cover it from 0 to its full length.
- Do NOT choose voices — only classify. Use "unknown" when unsure.

Return STRICT JSON ONLY in exactly this shape:
{{
  "characters": [
    {{ "character": "Alice", "gender": "female", "confidence": 0.92 }}
  ],
  "scenes": [
    {{ "scene_id": 1, "segments": [
        {{ "speaker": "Narrator", "start_char": 0, "end_char": 120 }},
        {{ "speaker": "Alice", "start_char": 120, "end_char": 210 }}
    ] }}
  ]
}}
{correction}
SCENES:
{scenes_block}
"""

CORRECTION_TEMPLATE = (
    "\nYour previous response was rejected: {reason}. "
    "Return corrected STRICT JSON that satisfies every constraint above.\n"
)


@dataclass
class SegmentResult:
    scenes: list[Scene]
    fallback_used: bool
    retry_count: int
    repairs: int
    characters: dict  # name -> {"gender", "confidence"}; {} when none/fallback


_VALID_GENDERS = {"male", "female", "unknown"}


def _parse_characters(output: dict) -> dict:
    """Extract {name: {gender, confidence}} from the LLM output, defensively."""
    meta: dict = {}
    for entry in output.get("characters") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("character")
        if not isinstance(name, str) or not name.strip():
            continue
        gender = entry.get("gender")
        if gender not in _VALID_GENDERS:
            gender = "unknown"
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        meta[name.strip()] = {"gender": gender, "confidence": confidence}
    return meta


def _normalize_speaker(speaker) -> str:
    if not isinstance(speaker, str):
        return NARRATOR
    speaker = speaker.strip()
    return speaker if speaker else NARRATOR


def _narrator_segment(scene: Scene) -> Segment:
    return {
        "speaker": NARRATOR,
        "text": scene["text"],
        "start_char": scene["start_char"],
        "end_char": scene["end_char"],
    }


def reconstruct_segments(scene: Scene, raw_segments) -> int:
    """Code truth layer: turn raw (scene-relative) segments into a gap-free,
    non-overlapping tiling of the scene, filling unattributed spans with Narrator.

    Mutates scene["segments"] (absolute offsets, sliced text) and scene["speakers"].
    Returns the number of repairs applied (clamps, drops, gap/overlap fixes).
    """
    scene_text = scene["text"]
    L = len(scene_text)
    base = scene["start_char"]
    repairs = 0

    # Collect valid (start, end, speaker) relative spans.
    spans = []
    if isinstance(raw_segments, list):
        for seg in raw_segments:
            if not isinstance(seg, dict):
                repairs += 1
                continue
            start = seg.get("start_char")
            end = seg.get("end_char")
            if not isinstance(start, int) or not isinstance(end, int):
                repairs += 1
                continue
            cs, ce = max(0, min(start, L)), max(0, min(end, L))
            if cs != start or ce != end:
                repairs += 1
            if cs >= ce:
                repairs += 1
                continue
            spans.append((cs, ce, _normalize_speaker(seg.get("speaker"))))

    spans.sort(key=lambda s: s[0])

    # Walk left→right, filling gaps with Narrator and trimming overlaps.
    tiled: list[tuple[int, int, str]] = []
    cursor = 0
    for cs, ce, speaker in spans:
        if cs > cursor:  # gap → Narrator fill
            tiled.append((cursor, cs, NARRATOR))
            repairs += 1
        if cs < cursor:  # overlap → trim
            cs = cursor
            repairs += 1
        if cs >= ce:
            continue
        tiled.append((cs, ce, speaker))
        cursor = ce
    if cursor < L:  # trailing gap (also handles empty spans → whole-scene Narrator)
        if tiled:
            repairs += 1
        tiled.append((cursor, L, NARRATOR))

    scene["segments"] = [
        {
            "speaker": speaker,
            "text": scene_text[cs:ce],
            "start_char": base + cs,
            "end_char": base + ce,
        }
        for cs, ce, speaker in tiled
    ]
    # Ordered-unique speakers.
    seen: list[str] = []
    for _, _, speaker in tiled:
        if speaker not in seen:
            seen.append(speaker)
    scene["speakers"] = seen
    return repairs


def _apply_narrator_fallback(scenes: list[Scene]) -> None:
    for scene in scenes:
        scene["segments"] = [_narrator_segment(scene)]
        scene["speakers"] = [NARRATOR]


def _scenes_block(scenes: list[Scene]) -> str:
    parts = []
    for scene in scenes:
        parts.append(
            f"### Scene {scene['scene_id']} (length {len(scene['text'])})\n{scene['text']}"
        )
    return "\n\n".join(parts)


async def _request_segments(client: LLMClient, scenes: list[Scene], correction: str) -> str:
    user_prompt = USER_PROMPT_TEMPLATE.format(
        scenes_block=_scenes_block(scenes), correction=correction
    )
    return await client.generate(SYSTEM_PROMPT, user_prompt)


def extract_segments(scenes: list[Scene], clean_text: str) -> SegmentResult:
    """Call 2: attribute per-scene speaker segments via a single LLM call.

    Always returns populated scenes: on missing key or repeated failure, every
    scene gets a single Narrator segment (single-voice, but the pipeline runs).
    """
    client = LLMClient()
    if not scenes:
        return SegmentResult(scenes=scenes, fallback_used=True, retry_count=0, repairs=0, characters={})
    if not client.api_key:
        print("[chars] DEEPSEEK_API_KEY not set — Narrator-only attribution.")
        _apply_narrator_fallback(scenes)
        return SegmentResult(scenes=scenes, fallback_used=True, retry_count=0, repairs=0, characters={})

    correction = ""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw = asyncio.run(_request_segments(client, scenes, correction))
            output = json.loads(raw)
            by_id = {s.get("scene_id"): s.get("segments") for s in output.get("scenes", [])}
            total_repairs = 0
            for scene in scenes:
                total_repairs += reconstruct_segments(scene, by_id.get(scene["scene_id"]))
            characters = _parse_characters(output)
            print(f"[chars] Attributed segments on attempt {attempt} "
                  f"({total_repairs} repairs, {len(characters)} classified).")
            return SegmentResult(
                scenes=scenes,
                fallback_used=False,
                retry_count=attempt - 1,
                repairs=total_repairs,
                characters=characters,
            )
        except json.JSONDecodeError:
            reason = "the response was not valid JSON"
            print(f"[chars] Attempt {attempt}: invalid JSON from LLM.")
        except LLMError as e:
            reason = f"the request errored ({e})"
            print(f"[chars] Attempt {attempt}: LLM error: {e}")
        correction = CORRECTION_TEMPLATE.format(reason=reason)

    print("[chars] All attempts failed — Narrator-only attribution.")
    _apply_narrator_fallback(scenes)
    return SegmentResult(scenes=scenes, fallback_used=True, retry_count=MAX_RETRIES + 1, repairs=0, characters={})
