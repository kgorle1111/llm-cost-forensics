"""Multi-tenancy: scoped API keys, one per team, hashed at rest.

Callers hit the proxy with  Authorization: Bearer cf_...  — the key maps to a
team, which becomes the attribution + dashboard access boundary. Provider keys
(OpenAI/Anthropic) live server-side in env and are never exposed to callers.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import HTTPException, Request

from .store import SessionLocal, Team


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def mint_key(name: str, monthly_budget_usd: float = 0.0, is_admin: bool = False) -> str:
    """Create a team + key. The plaintext key is returned ONCE, only the hash is stored."""
    key = "cf_" + secrets.token_hex(24)
    with SessionLocal() as s:
        existing = s.query(Team).filter_by(name=name).one_or_none()
        if existing:
            existing.key_hash = hash_key(key)
            existing.monthly_budget_usd = monthly_budget_usd
            existing.is_admin = is_admin
        else:
            s.add(Team(name=name, key_hash=hash_key(key),
                       monthly_budget_usd=monthly_budget_usd, is_admin=is_admin))
        s.commit()
    return key


def team_from_request(request: Request) -> Team:
    auth = request.headers.get("authorization", "")
    key = auth.removeprefix("Bearer ").strip()
    if not key.startswith("cf_"):
        raise HTTPException(401, "missing or malformed proxy key (expected 'Bearer cf_...')")
    with SessionLocal() as s:
        team = s.query(Team).filter_by(key_hash=hash_key(key)).one_or_none()
    if team is None:
        raise HTTPException(401, "unknown proxy key")
    return team
