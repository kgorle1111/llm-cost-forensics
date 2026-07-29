"""Quality-delta evaluator (LLM-as-judge).

Before suggesting "swap model A for cheaper model B on feature X", run both
models on a sample of real logged prompts and have a strong judge model score
the pairs. The result (win rate + mean delta) is stored and attached to every
substitution suggestion, so no one is told to change models blind.

Requires PROMPT_LOGGING=full (prompts must be stored to replay them) and a
real ANTHROPIC_API_KEY for the judge. Runs on samples, never on live traffic.
"""
from __future__ import annotations

import json
import re

import httpx

from . import config
from .store import Event, QualityDelta, SessionLocal

JUDGE_PROMPT = """You are comparing two AI assistant responses to the same request.

REQUEST:
{prompt}

RESPONSE A:
{a}

RESPONSE B:
{b}

Score how well each response fulfils the request (accuracy, completeness,
clarity), each on 0-10. Reply with ONLY a JSON object:
{{"score_a": <0-10>, "score_b": <0-10>}}"""


def sample_prompts(feature: str, n: int = 20) -> list[str]:
    with SessionLocal() as s:
        rows = (s.query(Event.prompt_text)
                .filter(Event.feature == feature, Event.prompt_text != "")
                .order_by(Event.ts.desc()).limit(n * 5).all())
    seen, out = set(), []
    for (p,) in rows:
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= n:
            break
    return out


def _call_model(client: httpx.Client, provider: str, model: str, prompt: str) -> str:
    if provider == "anthropic":
        r = client.post(
            config.ANTHROPIC_BASE_URL + "/v1/messages",
            headers={"x-api-key": config.ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": 1024,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    r = client.post(
        config.OPENAI_BASE_URL + "/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_judge_scores(text: str) -> tuple[float, float] | None:
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return float(obj["score_a"]), float(obj["score_b"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def verdict(win_rate_b: float, mean_delta: float) -> str:
    if win_rate_b >= 0.8 and mean_delta >= -0.5:
        return "safe"
    if win_rate_b >= 0.6 and mean_delta >= -1.5:
        return "risky"
    return "unsafe"


def evaluate(feature: str, provider: str, model_a: str, model_b: str,
             n_samples: int = 20) -> QualityDelta:
    prompts = sample_prompts(feature, n_samples)
    if not prompts:
        raise SystemExit(
            f"no stored prompts for feature '{feature}'. "
            "Set PROMPT_LOGGING=full on the proxy and collect some traffic first.")

    scores = []
    with httpx.Client(timeout=120) as client:
        for p in prompts:
            out_a = _call_model(client, provider, model_a, p)
            out_b = _call_model(client, provider, model_b, p)
            j = _call_model(client, "anthropic", config.JUDGE_MODEL,
                            JUDGE_PROMPT.format(prompt=p[:4000], a=out_a[:4000], b=out_b[:4000]))
            parsed = parse_judge_scores(j)
            if parsed:
                scores.append(parsed)

    if not scores:
        raise SystemExit("judge returned no parseable scores")
    win_rate_b = sum(1 for a, b in scores if b >= a) / len(scores)
    mean_delta = sum(b - a for a, b in scores) / len(scores)
    row = QualityDelta(feature=feature, model_a=model_a, model_b=model_b,
                       samples=len(scores), win_rate_b=round(win_rate_b, 3),
                       quality_delta=round(mean_delta, 3),
                       verdict=verdict(win_rate_b, mean_delta))
    with SessionLocal() as s:
        s.add(row)
        s.commit()
    return row
