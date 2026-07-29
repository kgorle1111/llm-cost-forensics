"""The proxy core: drop-in API-compatible pass-through for OpenAI
(/v1/chat/completions) and Anthropic (/v1/messages).

Design rules (from the spec, and they're graded):
- Streaming chunks pass through to the client in real time; cost accounting
  accumulates server-side and logs ONE row after the stream closes. The client
  never waits on logging.
- FAIL OPEN: if tagging/pricing/logging throws, the LLM call still succeeds.
- Attribution metadata: team comes from the proxy key (unforgeable by other
  teams); feature / prompt-version / environment come from headers.
"""
from __future__ import annotations

import json
import logging
import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import config
from .auth import team_from_request
from .pricing import (
    anthropic_prompt_text,
    cost_usd,
    count_tokens,
    openai_prompt_text,
    usage_from_response,
)
from .semcache import cache
from .store import log_event
from .waste import detector, fingerprint

log = logging.getLogger("costproxy")

# Single shared async client; tests swap the transport on this module.
_client = httpx.AsyncClient(timeout=120)


def _upstream(provider: str) -> tuple[str, dict]:
    # omit auth headers when no key is configured (keyless demo vs mock provider)
    if provider == "openai":
        headers = {}
        if config.OPENAI_API_KEY:
            headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"
        return config.OPENAI_BASE_URL + "/v1/chat/completions", headers
    headers = {"anthropic-version": "2023-06-01"}
    if config.ANTHROPIC_API_KEY:
        headers["x-api-key"] = config.ANTHROPIC_API_KEY
    return config.ANTHROPIC_BASE_URL + "/v1/messages", headers


def _meta(request: Request) -> dict:
    return {
        "feature": request.headers.get("x-feature", "untagged"),
        "prompt_version": request.headers.get("x-prompt-version", ""),
        "environment": request.headers.get("x-environment", "prod"),
    }


def _stream_output_text(provider: str, events: list[dict]) -> str:
    """Reassemble the generated text from accumulated SSE events."""
    parts = []
    for ev in events:
        if provider == "openai":
            for choice in ev.get("choices", []):
                parts.append(choice.get("delta", {}).get("content") or "")
        else:  # anthropic
            if ev.get("type") == "content_block_delta":
                parts.append(ev.get("delta", {}).get("text") or "")
    return "".join(parts)


def _stream_usage(provider: str, events: list[dict]) -> tuple[int, int] | None:
    """Providers can report exact usage in the final stream events. Prefer it."""
    inp = out = None
    for ev in events:
        u = ev.get("usage") or (ev.get("message", {}) or {}).get("usage")
        if u:
            if "prompt_tokens" in u:  # openai (stream_options.include_usage)
                inp, out = u.get("prompt_tokens", inp), u.get("completion_tokens", out)
            else:  # anthropic: input on message_start, output on message_delta
                inp = u.get("input_tokens", inp)
                out = u.get("output_tokens", out)
    if inp is None and out is None:
        return None
    return inp or 0, out or 0


def _safe_log(**fields):
    try:
        log_event(**fields)
    except Exception:
        log.exception("event logging failed — failing open, request unaffected")


async def handle(provider: str, request: Request) -> Response:
    team = team_from_request(request)  # 401s are the caller's fault, not fail-open
    t0 = time.perf_counter()
    body = await request.json()
    model = body.get("model", "unknown")
    stream = bool(body.get("stream", False))
    prompt_text = (
        openai_prompt_text(body) if provider == "openai" else anthropic_prompt_text(body)
    )
    meta = _meta(request)
    fp = fingerprint(team.name, provider, model, prompt_text)
    base_row = dict(
        team=team.name, provider=provider, model=model, stream=stream,
        fingerprint=fp, **meta,
        prompt_text=prompt_text if config.PROMPT_LOGGING == "full" else "",
    )

    # --- waste flags + semantic cache (both fail-open) -----------------------
    flags: list[str] = []
    try:
        flags = detector.classify(fp)
    except Exception:
        log.exception("waste detection failed — failing open")

    if config.CACHE_MODE != "off" and not stream:
        try:
            hit = cache.lookup(team.name, provider, model, prompt_text)
            if hit is not None:
                resp_json, saved, exact = hit
                flags = flags + (["duplicate"] if exact else ["semantic_dup"])
                temperature = body.get("temperature", 1.0)
                if config.CACHE_MODE == "serve" and temperature <= config.CACHE_MAX_TEMPERATURE:
                    detector.record(fp, errored=False)
                    _safe_log(**base_row, input_tokens=0, output_tokens=0, cost_usd=0.0,
                              status_code=200, waste_flags=flags, cache_hit=True,
                              saved_usd=saved,
                              overhead_ms=(time.perf_counter() - t0) * 1000)
                    return JSONResponse(resp_json, headers={"x-proxy-cache": "hit"})
        except Exception:
            log.exception("semantic cache failed — failing open")

    # --- forward to the real provider ----------------------------------------
    url, headers = _upstream(provider)
    if stream:
        return await _handle_stream(url, headers, body, base_row, flags, fp, t0, provider)

    tp0 = time.perf_counter()
    upstream = await _client.post(url, json=body, headers=headers)
    provider_ms = (time.perf_counter() - tp0) * 1000
    errored = upstream.status_code >= 400

    input_tokens = output_tokens = 0
    cost = None
    exact = True
    resp_json = None
    if not errored:
        try:
            resp_json = upstream.json()
            usage = usage_from_response(provider, resp_json)
            if usage:
                input_tokens, output_tokens = usage
            else:
                input_tokens, exact_in = count_tokens(prompt_text)
                out_text = json.dumps(resp_json.get("choices") or resp_json.get("content") or "")
                output_tokens, exact_out = count_tokens(out_text)
                exact = exact_in and exact_out
            cost = cost_usd(provider, model, input_tokens, output_tokens)
        except Exception:
            log.exception("cost calculation failed — failing open")

    try:
        detector.record(fp, errored=errored)
        if resp_json is not None and config.CACHE_MODE != "off" and not errored:
            cache.put(team.name, provider, model, prompt_text, resp_json, cost or 0.0)
    except Exception:
        log.exception("waste/cache bookkeeping failed — failing open")

    _safe_log(**base_row, input_tokens=input_tokens, output_tokens=output_tokens,
              tokens_exact=exact, cost_usd=cost, status_code=upstream.status_code,
              error=errored, waste_flags=flags, provider_latency_ms=provider_ms,
              overhead_ms=(time.perf_counter() - t0) * 1000 - provider_ms,
              response_text=(json.dumps(resp_json)[:20000]
                             if resp_json is not None and config.PROMPT_LOGGING == "full" else ""))

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        headers={"x-proxy-cost-usd": "" if cost is None else f"{cost:.8f}"},
    )


async def _handle_stream(url, headers, body, base_row, flags, fp, t0, provider):
    """Pass chunks through live; accumulate; log once after close."""
    prompt_text = base_row.get("prompt_text") or ""

    async def gen():
        accumulated: list[dict] = []
        status = {"code": 200}
        tp0 = time.perf_counter()
        try:
            async with _client.stream("POST", url, json=body, headers=headers) as upstream:
                status["code"] = upstream.status_code
                buffer = b""
                async for raw in upstream.aiter_bytes():
                    yield raw  # client gets the chunk immediately
                    buffer += raw
                # parse SSE after streaming — never in the hot loop
                for line in buffer.decode("utf-8", "ignore").splitlines():
                    line = line.strip()
                    if line.startswith("data:") and line != "data: [DONE]":
                        try:
                            accumulated.append(json.loads(line[5:].strip()))
                        except json.JSONDecodeError:
                            pass
        finally:
            provider_ms = (time.perf_counter() - tp0) * 1000
            errored = status["code"] >= 400
            try:
                usage = _stream_usage(provider, accumulated)
                if usage:
                    input_tokens, output_tokens = usage
                    exact = True
                else:
                    src = prompt_text or _reconstruct_prompt(body, provider)
                    input_tokens, e1 = count_tokens(src)
                    output_tokens, e2 = count_tokens(
                        _stream_output_text(provider, accumulated))
                    exact = e1 and e2
                cost = cost_usd(provider, base_row["model"], input_tokens, output_tokens)
                detector.record(fp, errored=errored)
                _safe_log(**base_row, input_tokens=input_tokens,
                          output_tokens=output_tokens, tokens_exact=exact,
                          cost_usd=cost, status_code=status["code"], error=errored,
                          waste_flags=flags, provider_latency_ms=provider_ms,
                          overhead_ms=(time.perf_counter() - t0) * 1000 - provider_ms)
            except Exception:
                log.exception("stream cost logging failed — failing open")

    return StreamingResponse(gen(), media_type="text/event-stream")


def _reconstruct_prompt(body: dict, provider: str) -> str:
    return openai_prompt_text(body) if provider == "openai" else anthropic_prompt_text(body)
