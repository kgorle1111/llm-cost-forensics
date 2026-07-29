"""LLM Cost Forensics — drop-in proxy + dashboard.

Client apps change ONE thing: base_url →  http://this-proxy:8080/openai/v1
(or /anthropic/v1) and use their team's cf_ key as the API key. Everything
else — tagging, pricing, waste detection, caching, logging — happens here.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse

from . import aggregate, proxy
from .auth import team_from_request
from .dashboard import DASHBOARD_HTML
from .store import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    await proxy._client.aclose()


app = FastAPI(title="LLM Cost Forensics", lifespan=lifespan)


# --- drop-in provider-compatible endpoints -----------------------------------
# OpenAI SDK:    base_url="http://proxy:8080/openai/v1"
# Anthropic SDK: base_url="http://proxy:8080/anthropic"
@app.post("/openai/v1/chat/completions")
async def openai_chat(request: Request):
    return await proxy.handle("openai", request)


@app.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request):
    return await proxy.handle("anthropic", request)


# Bare aliases so `base_url=http://proxy:8080/v1` also works for OpenAI-style
@app.post("/v1/chat/completions")
async def openai_chat_alias(request: Request):
    return await proxy.handle("openai", request)


@app.post("/v1/messages")
async def anthropic_alias(request: Request):
    return await proxy.handle("anthropic", request)


# --- dashboard + aggregation API ---------------------------------------------
def _scope(request: Request) -> str | None:
    """Admin keys see everything; team keys see only their own rows."""
    team = team_from_request(request)
    return None if team.is_admin else team.name


@app.get("/api/summary")
def api_summary(scope: str | None = Depends(_scope)):
    return aggregate.summary(scope)


@app.get("/api/trend")
def api_trend(scope: str | None = Depends(_scope)):
    return aggregate.daily_trend(team=scope)


@app.get("/api/by/{dim}")
def api_by(dim: str, scope: str | None = Depends(_scope)):
    if dim not in ("feature", "team", "model"):
        return []
    if dim == "team" and scope is not None:
        return aggregate.by_dimension("team", scope)  # only their own slice
    return aggregate.by_dimension(dim, scope)


@app.get("/api/waste")
def api_waste(scope: str | None = Depends(_scope)):
    return aggregate.waste_report(scope)


@app.get("/api/suggestions")
def api_suggestions(scope: str | None = Depends(_scope)):
    return aggregate.suggestions(scope)


@app.get("/api/alerts")
def api_alerts(scope: str | None = Depends(_scope)):
    all_alerts = aggregate.alerts()
    return all_alerts if scope is None else [a for a in all_alerts if a["team"] == scope]


@app.get("/dashboard")
def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/healthz")
def healthz():
    return {"ok": True}
