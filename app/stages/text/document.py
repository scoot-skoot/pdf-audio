import re
from typing import TypedDict

# Stage 0: deterministic document boundary split. Operates on RAW text (newlines
# intact) so line-oriented markers are detectable. No LLM. Conservative: when
# markers are absent or the split would gut the body, everything is main_content.

FRONT_MARKERS = [
    "table of contents",
    "contents",
    "preface",
    "foreword",
    "introduction",
    "copyright",
    "all rights reserved",
    "translated by",
]

BACK_MARKERS = [
    "references",
    "bibliography",
    "appendix",
    "works cited",
    "index",
    "about the author",
    "acknowledgments",
    "acknowledgements",
]

# A marker only counts as front matter if it appears within this leading fraction,
# back matter only within this trailing fraction.
FRONT_WINDOW = 0.20
BACK_WINDOW = 0.30
# If the detected main content drops below this fraction of the text, abandon the
# split and treat the whole document as main content.
MIN_MAIN_FRACTION = 0.50


class DocumentSegments(TypedDict):
    front_matter: str
    main_content: str
    back_matter: str


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    """Return (line_start, line_end_exclusive) for the line containing pos."""
    start = text.rfind("\n", 0, pos) + 1  # 0 if not found
    nl = text.find("\n", pos)
    end = len(text) if nl == -1 else nl + 1
    return start, end


def _front_boundary(text: str) -> int:
    """End offset of front matter (0 if none): end of the last front-marker line
    within the leading window."""
    window = int(len(text) * FRONT_WINDOW)
    lowered = text.lower()
    boundary = 0
    for marker in FRONT_MARKERS:
        idx = 0
        while True:
            idx = lowered.find(marker, idx, window)
            if idx == -1:
                break
            _, line_end = _line_bounds(text, idx)
            boundary = max(boundary, line_end)
            idx += len(marker)
    return boundary


def _back_boundary(text: str) -> int:
    """Start offset of back matter (len(text) if none): start of the first
    back-marker line within the trailing window."""
    window_start = int(len(text) * (1 - BACK_WINDOW))
    lowered = text.lower()
    boundary = len(text)
    for marker in BACK_MARKERS:
        idx = lowered.find(marker, window_start)
        if idx != -1:
            line_start, _ = _line_bounds(text, idx)
            boundary = min(boundary, line_start)
    return boundary


def split_document(raw_text: str) -> DocumentSegments:
    """Split raw extracted text into front / main / back matter (deterministic)."""
    n = len(raw_text)
    if n == 0:
        return {"front_matter": "", "main_content": "", "back_matter": ""}

    front_end = _front_boundary(raw_text)
    back_start = _back_boundary(raw_text)

    # Guard against degenerate splits.
    if back_start <= front_end or (back_start - front_end) < n * MIN_MAIN_FRACTION:
        return {"front_matter": "", "main_content": raw_text, "back_matter": ""}

    return {
        "front_matter": raw_text[:front_end],
        "main_content": raw_text[front_end:back_start],
        "back_matter": raw_text[back_start:],
    }
