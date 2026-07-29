"""Event store: every proxied call is one row. SQLite by default, Postgres via
DATABASE_URL. Writes are fail-open — a logging failure must never break the
user's LLM call (that rule is enforced in proxy.py, tested in test_failopen.py).
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import DATABASE_URL

log = logging.getLogger("costproxy")

Base = declarative_base()


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=dt.datetime.utcnow, index=True)
    team = Column(String, index=True)
    feature = Column(String, index=True)
    prompt_version = Column(String, default="")
    environment = Column(String, default="prod")
    provider = Column(String)
    model = Column(String, index=True)
    stream = Column(Boolean, default=False)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    tokens_exact = Column(Boolean, default=True)
    cost_usd = Column(Float)  # None => model missing from rate card
    provider_latency_ms = Column(Float, default=0)
    overhead_ms = Column(Float, default=0)
    status_code = Column(Integer, default=200)
    error = Column(Boolean, default=False)
    fingerprint = Column(String, index=True)
    waste_flags = Column(JSON, default=list)
    cache_hit = Column(Boolean, default=False)
    saved_usd = Column(Float, default=0.0)  # spend avoided by a cache hit
    prompt_text = Column(Text, default="")  # only when PROMPT_LOGGING=full
    response_text = Column(Text, default="")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    key_hash = Column(String, unique=True, index=True)  # sha256, never plaintext
    monthly_budget_usd = Column(Float, default=0.0)  # 0 = no budget alerting
    is_admin = Column(Boolean, default=False)


class QualityDelta(Base):
    __tablename__ = "quality_deltas"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=dt.datetime.utcnow)
    feature = Column(String, index=True)
    model_a = Column(String)  # current (expensive) model
    model_b = Column(String)  # candidate cheaper model
    samples = Column(Integer)
    win_rate_b = Column(Float)  # fraction of pairs where B was >= A
    quality_delta = Column(Float)  # mean judge score delta (B - A), -10..10 scale
    verdict = Column(String)  # safe | risky | unsafe


def make_engine(url: str = DATABASE_URL):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, pool_pre_ping=True, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)


def log_event(**fields) -> None:
    """Fail-open write: exceptions are logged and swallowed by the caller."""
    with SessionLocal() as s:
        s.add(Event(**fields))
        s.commit()


def session() -> Session:
    return SessionLocal()
