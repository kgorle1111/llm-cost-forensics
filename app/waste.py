"""Waste detection: retries and duplicates via request fingerprinting.

A fingerprint is a hash of (team, provider, model, full prompt). Seeing the
same fingerprint again inside a window means either a retry (previous attempt
errored) or a duplicate (previous attempt succeeded — a cache should have
served it). Legitimate distinct calls have distinct fingerprints and are never
flagged, which the golden tests in tests/test_waste.py assert both ways.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque

from .config import DUP_WINDOW_S, RETRY_WINDOW_S


def fingerprint(team: str, provider: str, model: str, prompt_text: str) -> str:
    raw = json.dumps([team, provider, model, prompt_text], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class WasteDetector:
    """In-memory sliding window of recent fingerprints.

    ponytail: per-process memory, resets on restart — fine for one proxy node.
    Move the window into Redis when you run multiple replicas.
    """

    def __init__(self):
        self._seen: dict[str, deque] = {}  # fp -> deque[(ts, errored)]

    def classify(self, fp: str) -> list[str]:
        """Return waste flags for THIS request, based on prior sightings."""
        now = time.time()
        flags: list[str] = []
        history = self._seen.get(fp)
        if history:
            while history and now - history[0][0] > max(RETRY_WINDOW_S, DUP_WINDOW_S):
                history.popleft()
            for ts, errored in history:
                if errored and now - ts <= RETRY_WINDOW_S:
                    flags.append("retry")
                    break
            for ts, errored in history:
                if not errored and now - ts <= DUP_WINDOW_S:
                    flags.append("duplicate")
                    break
        return flags

    def record(self, fp: str, errored: bool) -> None:
        self._seen.setdefault(fp, deque(maxlen=50)).append((time.time(), errored))


detector = WasteDetector()
