"""Aggregation layer: turns raw event rows into the numbers the dashboard
shows. Runs off the request path — a slow rollup can never slow a live call.

ponytail: SQL group-bys computed on demand — plenty fast into the millions of
rows on SQLite/Postgres. Add materialized rollup tables (or move to
ClickHouse) when dashboard loads get slow, not before.
"""
from __future__ import annotations

import datetime as dt
import statistics

from sqlalchemy import func

from .config import rate_card
from .store import Event, QualityDelta, SessionLocal, Team

WASTE_FLAGS = ("retry", "duplicate", "semantic_dup")


def _scope(q, team: str | None):
    return q.filter(Event.team == team) if team else q


def summary(team: str | None = None) -> dict:
    with SessionLocal() as s:
        base = _scope(s.query(Event), team)
        events = base.all()
        total = sum(e.cost_usd or 0 for e in events)
        waste = sum(
            e.cost_usd or 0 for e in events
            if any(f in WASTE_FLAGS for f in (e.waste_flags or []))
        )
        saved = sum(e.saved_usd or 0 for e in events)
        overheads = sorted(e.overhead_ms or 0 for e in events if not e.cache_hit)
        unpriced = sum(1 for e in events if e.cost_usd is None and not e.error)

        def pct(p):
            if not overheads:
                return 0.0
            return overheads[min(len(overheads) - 1, int(p / 100 * len(overheads)))]

        return {
            "calls": len(events),
            "total_cost_usd": round(total, 4),
            "waste_cost_usd": round(waste, 4),
            "waste_pct": round(100 * waste / total, 1) if total else 0.0,
            "cache_saved_usd": round(saved, 4),
            "errors": sum(1 for e in events if e.error),
            "cache_hits": sum(1 for e in events if e.cache_hit),
            "unpriced_calls": unpriced,
            "proxy_overhead_ms_p50": round(pct(50), 1),
            "proxy_overhead_ms_p99": round(pct(99), 1),
        }


def by_dimension(dim: str, team: str | None = None) -> list[dict]:
    col = {"feature": Event.feature, "team": Event.team, "model": Event.model}[dim]
    with SessionLocal() as s:
        rows = _scope(
            s.query(col, func.count(Event.id), func.sum(Event.cost_usd)), team
        ).group_by(col).all()
    return sorted(
        [{dim: r[0], "calls": r[1], "cost_usd": round(r[2] or 0, 4)} for r in rows],
        key=lambda r: -r["cost_usd"],
    )


def daily_trend(days: int = 14, team: str | None = None) -> list[dict]:
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    with SessionLocal() as s:
        events = _scope(s.query(Event).filter(Event.ts >= since), team).all()
    buckets: dict[str, float] = {}
    for e in events:
        buckets.setdefault(e.ts.strftime("%Y-%m-%d"), 0.0)
        buckets[e.ts.strftime("%Y-%m-%d")] += e.cost_usd or 0
    return [{"day": d, "cost_usd": round(c, 4)} for d, c in sorted(buckets.items())]


def waste_report(team: str | None = None) -> list[dict]:
    """Waste grouped by (feature, flag) — the '$9k of it was retries in
    checkout-summarizer' artifact."""
    with SessionLocal() as s:
        events = _scope(s.query(Event), team).all()
    agg: dict[tuple, dict] = {}
    for e in events:
        for f in e.waste_flags or []:
            if f not in WASTE_FLAGS:
                continue
            k = (e.feature, f)
            a = agg.setdefault(k, {"feature": e.feature, "flag": f, "calls": 0, "cost_usd": 0.0})
            a["calls"] += 1
            a["cost_usd"] += e.cost_usd or 0
    out = sorted(agg.values(), key=lambda r: -r["cost_usd"])
    for r in out:
        r["cost_usd"] = round(r["cost_usd"], 4)
    return out


def suggestions(team: str | None = None) -> list[dict]:
    """Cheaper-model substitutions with quality evidence attached.

    Never a bare cost number: every row carries the judge's verdict or an
    explicit 'unevaluated' so nobody swaps models blind.
    """
    out = []
    with SessionLocal() as s:
        spend = _scope(
            s.query(Event.feature, Event.provider, Event.model,
                    func.sum(Event.input_tokens), func.sum(Event.output_tokens),
                    func.sum(Event.cost_usd)), team
        ).group_by(Event.feature, Event.provider, Event.model).all()
        deltas = {(d.feature, d.model_a, d.model_b): d for d in s.query(QualityDelta).all()}

    for feature, provider, model, in_tok, out_tok, cost in spend:
        entry = rate_card.get(provider, model)
        if not entry or "cheaper_alternative" not in entry or not cost:
            continue
        alt = entry["cheaper_alternative"]
        alt_entry = rate_card.get(provider, alt)
        if not alt_entry:
            continue
        alt_cost = ((in_tok or 0) * alt_entry["input_per_1m"]
                    + (out_tok or 0) * alt_entry["output_per_1m"]) / 1_000_000
        d = deltas.get((feature, model, alt))
        out.append({
            "feature": feature,
            "current_model": model,
            "suggested_model": alt,
            "current_cost_usd": round(cost, 4),
            "projected_cost_usd": round(alt_cost, 4),
            "monthly_savings_usd": round(cost - alt_cost, 4),
            "quality": (
                {"verdict": d.verdict, "win_rate": d.win_rate_b,
                 "delta": d.quality_delta, "samples": d.samples}
                if d else {"verdict": "unevaluated",
                           "how": "run: python scripts/judge_run.py --feature "
                                  f"'{feature}' --candidate {alt}"}
            ),
        })
    return sorted(out, key=lambda r: -r["monthly_savings_usd"])


def alerts() -> list[dict]:
    """Budget-threshold alerts, month to date."""
    month_start = dt.datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    out = []
    with SessionLocal() as s:
        for t in s.query(Team).filter(Team.monthly_budget_usd > 0).all():
            mtd = (s.query(func.sum(Event.cost_usd))
                   .filter(Event.team == t.name, Event.ts >= month_start).scalar()) or 0
            pct = 100 * mtd / t.monthly_budget_usd
            if pct >= 80:
                out.append({
                    "team": t.name, "mtd_usd": round(mtd, 2),
                    "budget_usd": t.monthly_budget_usd, "pct": round(pct, 1),
                    "level": "critical" if pct >= 100 else "warning",
                })
    return out
