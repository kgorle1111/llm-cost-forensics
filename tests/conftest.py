"""Test bootstrap: isolated temp DB + a mocked provider transport.

Env must be set BEFORE app modules import (config reads env at import time).
No test ever hits a real paid API.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmpdir = tempfile.mkdtemp(prefix="costforensics-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["PROMPT_LOGGING"] = "redacted"

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import proxy  # noqa: E402
from app.auth import mint_key  # noqa: E402
from app.main import app  # noqa: E402
from app.semcache import cache  # noqa: E402
from app.store import Event, SessionLocal, init_db  # noqa: E402
from app.waste import detector  # noqa: E402


def mock_handler(request: httpx.Request) -> httpx.Response:
    """Stand-in for OpenAI/Anthropic. Mirrors their response shapes."""
    body = json.loads(request.content)
    prompt = json.dumps(body.get("messages", [])) + str(body.get("system", ""))
    if "FAIL" in prompt:
        return httpx.Response(500, json={"error": {"message": "upstream boom"}})

    if request.url.path.endswith("/chat/completions"):
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        if body.get("stream"):
            chunks = (
                'data: {"choices":[{"delta":{"content":"Hello "},"index":0}]}\n\n'
                'data: {"choices":[{"delta":{"content":"world"},"index":0}]}\n\n'
                f'data: {json.dumps({"choices": [], "usage": usage})}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=chunks.encode(),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "cmpl-1", "object": "chat.completion", "model": body.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello world"},
                         "finish_reason": "stop"}],
            "usage": usage})

    usage = {"input_tokens": 80, "output_tokens": 40}
    if body.get("stream"):
        chunks = (
            f'data: {json.dumps({"type": "message_start", "message": {"usage": {"input_tokens": 80}}})}\n\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi there"}}\n\n'
            f'data: {json.dumps({"type": "message_delta", "usage": {"output_tokens": 40}})}\n\n'
        )
        return httpx.Response(200, content=chunks.encode(),
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={
        "id": "msg-1", "type": "message", "model": body.get("model"),
        "content": [{"type": "text", "text": "Hi there"}], "usage": usage})


@pytest.fixture()
def client():
    init_db()
    proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    cache.clear()
    detector._seen.clear()
    with SessionLocal() as s:
        s.query(Event).delete()
        s.commit()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def team_key():
    init_db()
    return mint_key("test-team", monthly_budget_usd=10.0)


def events():
    with SessionLocal() as s:
        return s.query(Event).order_by(Event.id).all()
