import re

# v1 heuristic mode detection (no LLM, deterministic). Scores dialogue density
# against technical/code density, both normalized per 1000 characters. The
# default is intentionally biased toward "structured": narrative is only chosen
# when the text is clearly dialogue-dominant.

# Weight applied to the technical density when subtracting it from dialogue.
TECHNICAL_WEIGHT = 1.0
# Minimum (dialogue - weighted technical) score per 1000 chars to call it narrative.
NARRATIVE_THRESHOLD = 3.0

# Quotation marks (straight + curly) and dialogue dashes signal prose dialogue.
_DIALOGUE_CHARS = '"“”‘’—'
# Math / code / markup symbols signal technical material.
_TECHNICAL_CHARS = "=<>{}|^%\\*+/[]"


def _per_1000(count: int, length: int) -> float:
    return (count / length) * 1000 if length else 0.0


def detect_mode(text: str) -> str:
    """Heuristically classify text as "narrative" or "structured".

    Deterministic and LLM-free. Biased toward "structured" — returns "narrative"
    only when dialogue signals clearly outweigh technical/code signals.
    """
    length = len(text)
    if length == 0:
        return "structured"

    dialogue_count = sum(text.count(c) for c in _DIALOGUE_CHARS)
    # Leading "- " on a line is a common dialogue marker.
    dialogue_count += len(re.findall(r"(?m)^\s*-\s", text))

    technical_count = sum(text.count(c) for c in _TECHNICAL_CHARS)
    technical_count += sum(c.isdigit() for c in text)

    dialogue_density = _per_1000(dialogue_count, length)
    technical_density = _per_1000(technical_count, length)

    score = dialogue_density - TECHNICAL_WEIGHT * technical_density
    return "narrative" if score >= NARRATIVE_THRESHOLD else "structured"
