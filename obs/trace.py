import json
import os
import time


class Trace:
    """Append-only event log for one pipeline run.

    Each event records a stage, a wall-clock timestamp, and arbitrary fields.
    Named 'obs.trace' to avoid clashing with the stdlib `trace` module.
    """

    def __init__(self):
        self._events: list[dict] = []
        self._t0 = time.perf_counter()

    def event(self, stage: str, **fields):
        self._events.append(
            {"stage": stage, "t": round(time.perf_counter() - self._t0, 4), **fields}
        )

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._events, f, indent=2)
