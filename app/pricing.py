"""Token counting + cost calculation.

Every dollar in the dashboard is downstream of this file being right.

Order of truth:
1. Provider-reported usage in the response body (this is what they bill on).
2. Real tokenizer (tiktoken) when a stream ends without a usage block.
3. chars/4 heuristic only if tiktoken is unavailable — flagged as estimated.
"""
from __future__ import annotations

from .config import rate_card

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:  # tiktoken missing or its model file unreachable
    _enc = None


def count_tokens(text: str) -> tuple[int, bool]:
    """Return (token_count, exact). exact=False means heuristic fallback."""
    if not text:
        return 0, True
    if _enc is not None:
        return len(_enc.encode(text)), True
    return max(1, len(text) // 4), False  # ponytail: chars/4, only if tiktoken absent


def cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float | None:
    """None means the model is missing from the rate card (logged, alerted)."""
    entry = rate_card.get(provider, model)
    if entry is None:
        return None
    return round(
        input_tokens * entry["input_per_1m"] / 1_000_000
        + output_tokens * entry["output_per_1m"] / 1_000_000,
        8,
    )


def openai_prompt_text(body: dict) -> str:
    parts = []
    for m in body.get("messages", []):
        c = m.get("content", "")
        parts.append(c if isinstance(c, str) else str(c))
    return "\n".join(parts)


def anthropic_prompt_text(body: dict) -> str:
    parts = []
    sys = body.get("system", "")
    if sys:
        parts.append(sys if isinstance(sys, str) else str(sys))
    for m in body.get("messages", []):
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        parts.append(str(c))
    return "\n".join(parts)


def usage_from_response(provider: str, resp_json: dict) -> tuple[int, int] | None:
    """Extract (input_tokens, output_tokens) from a provider response body."""
    u = resp_json.get("usage")
    if not u:
        return None
    if provider == "openai" or "prompt_tokens" in u:
        return u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    return u.get("input_tokens", 0), u.get("output_tokens", 0)
