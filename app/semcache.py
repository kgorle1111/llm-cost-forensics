"""Semantic cache: catches near-duplicate prompts that an exact-string cache
misses ("Summarize this ticket: ..." sent twice with trivial whitespace/casing
differences, or a re-worded but semantically identical request).

Similarity = Jaccard over 3-word shingles of the normalized prompt.
ponytail: shingle Jaccard, not embeddings — zero deps, offline, deterministic,
and tuned against a labeled should-hit/should-miss set (tests/test_semcache.py).
Upgrade path when recall matters: swap _shingles/_similarity for an embedding
model + cosine over Redis, same interface.

Cache entries are scoped per (team, provider, model) so one team's responses
can never be served to another team.
"""
from __future__ import annotations

import re
import time

from .config import CACHE_SIMILARITY, CACHE_TTL_S


def _normalize(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def _shingles(text: str) -> frozenset:
    words = _normalize(text)
    if len(words) < 3:
        return frozenset(words)
    return frozenset(tuple(words[i:i + 3]) for i in range(len(words) - 2))


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class SemanticCache:
    def __init__(self, ttl_s: float = CACHE_TTL_S, threshold: float = CACHE_SIMILARITY):
        self.ttl_s = ttl_s
        self.threshold = threshold
        # scope key -> list of (ts, prompt_text, shingles, response_json, cost_usd)
        self._entries: dict[tuple, list] = {}

    def _scope(self, team: str, provider: str, model: str) -> tuple:
        return (team, provider, model)

    def lookup(self, team: str, provider: str, model: str, prompt_text: str):
        """Return (response_json, saved_usd, exact) on hit, else None."""
        now = time.time()
        entries = self._entries.get(self._scope(team, provider, model), [])
        entries[:] = [e for e in entries if now - e[0] <= self.ttl_s]
        probe = _shingles(prompt_text)
        for ts, text, sh, resp, cost in reversed(entries):
            if text == prompt_text:
                return resp, cost, True
            union = len(probe | sh)
            if union and len(probe & sh) / union >= self.threshold:
                return resp, cost, False
        return None

    def put(self, team: str, provider: str, model: str, prompt_text: str,
            response_json: dict, cost_usd: float) -> None:
        scope = self._scope(team, provider, model)
        self._entries.setdefault(scope, []).append(
            (time.time(), prompt_text, _shingles(prompt_text), response_json, cost_usd or 0.0)
        )
        self._entries[scope] = self._entries[scope][-200:]  # bound memory

    def clear(self):
        self._entries.clear()


cache = SemanticCache()
