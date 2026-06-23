import asyncio
import json
import re
from dataclasses import dataclass

from app.stages.llm.client import LLMClient, LLMError

# Stage 0.5 (optional, --trim-matter): refine the deterministic main_content by
# trimming any front/back matter the keyword markers in text/document.py missed
# (e.g. a long dedication/epigraph or a trailing author's note). A single LLM call
# *proposes* verbatim boundary anchors + confidences; code locates those anchors in
# the text, gates on confidence, and slices at the found offsets (no paraphrase risk,
# never mid-word). Never aborts: any failure, missing key, low confidence, or an
# anchor that can't be located degrades to "no trimming" (the original main is kept).


@dataclass
class MatterResult:
    front_matter: str        # leading text trimmed off (saved, not narrated)
    main_content: str        # the kept body
    back_matter: str         # trailing text trimmed off (saved, not narrated)
    front_confidence: float  # 0.0-1.0
    back_confidence: float   # 0.0-1.0
    fallback_used: bool
    retry_count: int


# Number of LLM retries after the first attempt (total attempts = MAX_RETRIES + 1).
MAX_RETRIES = 2
# A boundary is only acted on when the model is at least this confident; below it,
# that side is preserved (never delete uncertain regions).
CONFIDENCE_THRESHOLD = 0.6
# If trimming would shrink the body below this fraction of the input, abandon the
# trim and preserve everything. Guards against gutting the actual book body.
MIN_MAIN_FRACTION = 0.5
# Cap how many leading words of a returned anchor we use to locate the boundary.
# An anchor is only a *marker* for where the body/back-matter begins; a handful of
# words uniquely pins it, and a shorter needle is far likelier to match verbatim.
ANCHOR_MAX_WORDS = 12


SYSTEM_PROMPT = (
    "You are a book front/back matter detection engine. You locate where the actual "
    "book body begins and ends within a block of text and return STRICT JSON ONLY, "
    "with no prose, no markdown, and no code fences."
)

# We ask for verbatim *anchor* text rather than character offsets: LLMs cannot count
# characters reliably (they emit plausible round numbers), but they can copy the exact
# words at a boundary. Code then locates those words in the text to get an offset that
# is correctly aligned — never mid-word. This is the project's "LLM decides meaning;
# code enforces correctness" split applied to matter trimming.
USER_PROMPT_TEMPLATE = """Find where the actual book body begins and ends in the text below.

Front matter (precedes the body) includes: title pages, copyright pages,
acknowledgements, dedications, prefaces, introductions, table of contents.
Back matter (follows the body) includes: references, bibliography, appendices,
index, glossary, author notes.
Main content is the actual book body (the story / chapters themselves).

Bias strongly toward KEEPING content: only trim text you are confident is matter,
and report LOW confidence whenever you are unsure. It is far better to leave some
matter in than to cut real body text.

Report two anchors, each copied WORD-FOR-WORD from the text (same spelling,
punctuation, and capitalization — do not paraphrase or summarize):
- front_anchor: the first ~6-12 words of the BODY (the point where front matter ends
  and the real content begins). Use "" (empty string) if there is no front matter.
- back_anchor: the first ~6-12 words of the BACK MATTER (the point where the body ends).
  Use "" (empty string) if there is no back matter.
Confidences are numbers between 0.0 and 1.0.

Return STRICT JSON ONLY in exactly this shape:
{{
  "front_anchor": "",
  "back_anchor": "",
  "front_confidence": 0.0,
  "back_confidence": 0.0,
  "front_reason": "short reason",
  "back_reason": "short reason"
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


def validate_matter(output: dict) -> bool:
    """Enforce structural guarantees on raw LLM output (anchors + confidences).

    Checks: dict shape, string anchors, and float confidences in [0, 1]. Anchor
    *location* (and confidence gating + the body-protection guard) are applied
    separately (in _build_result) — they shape the result rather than reject it,
    so an anchor that simply can't be found degrades to "preserve" not "retry".
    """
    if not isinstance(output, dict):
        return False

    if not isinstance(output.get("front_anchor"), str):
        return False
    if not isinstance(output.get("back_anchor"), str):
        return False

    for key in ("front_confidence", "back_confidence"):
        conf = output.get(key)
        # bool is an int subclass; accept ints too but reject booleans.
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            return False
        if not (0.0 <= conf <= 1.0):
            return False

    return True


def _locate_anchor(text: str, anchor: str, *, from_end: bool) -> int | None:
    """Return the character offset where ``anchor`` occurs in ``text``, or None.

    The anchor is matched whitespace-tolerantly (the LLM may normalize runs of
    spaces/newlines differently from the source) using only its first
    ``ANCHOR_MAX_WORDS`` words. ``from_end`` picks the last match instead of the
    first — used for the back-matter anchor so repeated phrases bias toward the end
    of the book. Returns None when the anchor is empty or not found, which the
    caller treats as "uncertain → preserve this side".
    """
    words = anchor.split()[:ANCHOR_MAX_WORDS]
    if not words:
        return None

    pattern = re.compile(r"\s+".join(re.escape(w) for w in words))
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    return (matches[-1] if from_end else matches[0]).start()


async def _request_matter(client: LLMClient, main_text: str, correction: str = "") -> str:
    user_prompt = USER_PROMPT_TEMPLATE.format(text=main_text, correction=correction)
    return await client.generate(SYSTEM_PROMPT, user_prompt)


def _fallback_result(main_text: str, retry_count: int) -> MatterResult:
    """Preserve everything: the whole input is main_content, nothing trimmed."""
    return MatterResult(
        front_matter="",
        main_content=main_text,
        back_matter="",
        front_confidence=0.0,
        back_confidence=0.0,
        fallback_used=True,
        retry_count=retry_count,
    )


def _build_result(output: dict, main_text: str, retry_count: int) -> MatterResult:
    """Locate the anchors, apply confidence gating + body-protection guard, slice.

    Offsets are derived by *finding the anchor text in main_text* (never trusting a
    model-supplied number), so a kept boundary always lands exactly between words.
    Returns a fallback (preserve everything) on low confidence, an anchor that can't
    be located, or a trim that would gut the body.
    """
    n = len(main_text)
    front_conf = float(output["front_confidence"])
    back_conf = float(output["back_confidence"])

    # Front boundary: only when confident AND the anchor is found in the text.
    front_end = 0
    if front_conf >= CONFIDENCE_THRESHOLD:
        idx = _locate_anchor(main_text, output["front_anchor"], from_end=False)
        if idx is not None:
            front_end = idx

    # Back boundary: only when confident, the anchor is found, and it lies after the
    # body start (so we never cross the two boundaries).
    back_start = n
    if back_conf >= CONFIDENCE_THRESHOLD:
        idx = _locate_anchor(main_text, output["back_anchor"], from_end=True)
        if idx is not None and idx > front_end:
            back_start = idx

    # Body-protection guard: never shrink the body below MIN_MAIN_FRACTION.
    if (back_start - front_end) < n * MIN_MAIN_FRACTION:
        return _fallback_result(main_text, retry_count)

    return MatterResult(
        front_matter=main_text[:front_end],
        main_content=main_text[front_end:back_start],
        back_matter=main_text[back_start:],
        front_confidence=front_conf,
        back_confidence=back_conf,
        fallback_used=False,
        retry_count=retry_count,
    )


def detect_matter(main_text: str) -> MatterResult:
    """Stage 0.5: trim residual front/back matter from main_text via one LLM call.

    Always returns a populated MatterResult. On missing API key, repeated invalid
    output, any network/LLM error, low confidence, or a trim that would gut the
    body, falls back to preserving the whole input unchanged. This keeps the
    pipeline deterministic-safe — it never aborts.
    """
    text_len = len(main_text)
    if text_len == 0:
        return _fallback_result(main_text, retry_count=0)

    client = LLMClient()
    if not client.api_key:
        print("[matter] DEEPSEEK_API_KEY not set — preserving all content (no trim).")
        return _fallback_result(main_text, retry_count=0)

    # On retries, feed the prior failure reason back into the prompt (prompt-
    # correction retry) so the model can fix its output.
    correction = ""
    reason = ""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw = asyncio.run(_request_matter(client, main_text, correction))
            output = json.loads(raw)
            if validate_matter(output):
                result = _build_result(output, main_text, retry_count=attempt - 1)
                removed = len(result.front_matter) + len(result.back_matter)
                print(
                    f"[matter] Detected on attempt {attempt}: "
                    f"front {len(result.front_matter)} / back {len(result.back_matter)} chars "
                    f"removed (conf {result.front_confidence:.2f}/{result.back_confidence:.2f})."
                    + ("" if removed else " Nothing trimmed.")
                )
                return result
            reason = "the JSON did not satisfy the matter constraints (anchors/confidences)"
            print(f"[matter] Attempt {attempt}: output failed validation.")
        except json.JSONDecodeError:
            reason = "the response was not valid JSON"
            print(f"[matter] Attempt {attempt}: invalid JSON from LLM.")
        except LLMError as e:
            reason = f"the request errored ({e})"
            print(f"[matter] Attempt {attempt}: LLM error: {e}")

        correction = CORRECTION_TEMPLATE.format(reason=reason)

    print("[matter] All attempts failed — preserving all content (no trim).")
    return _fallback_result(main_text, retry_count=MAX_RETRIES + 1)
