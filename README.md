> **Status: archived (October 2026). Experiment, not production-ready.**
>
> **What works:** a drop-in OpenAI/Anthropic-compatible proxy. On real traffic (tested through Ollama), its token counts matched provider-reported usage on both APIs, with and without streaming. Teams are kept separate and prompts are redacted by default.
>
> **Known limits:**
> - When the provider doesn't report usage, counts come from a single local tokenizer and are estimates.
> - Anthropic cache-read and cache-write tokens aren't priced.
> - Upstream errors on streaming requests come back as HTTP 200 with an empty stream.
> - The semantic cache ignores non-ASCII text, so keep `CACHE_MODE=detect` (the default).
> - The model-comparison judge has no answer-order swap and no minimum sample size.
> - When an exact duplicate is also a semantic-cache hit, it's flagged `duplicate` twice (`app/proxy.py:127`), so the waste report counts that call and its cost twice.

# 💸 Build 2 — LLM Cost Forensics

**FinOps for LLM spend.** A drop-in API proxy that turns one opaque LLM invoice into
per-team, per-feature, per-prompt-version cost attribution — with waste detection
(retries, duplicates, over-provisioned models) and cheaper-model substitution
suggestions backed by LLM-as-judge quality deltas.

> Existing tools tell you an LLM call happened and what it cost. This tells you
> whether that spend was worth it, who owns it, and what to do about it.

A team adopts it by changing **one URL** (their SDK `base_url`). Same request shape,
same response shape, same streaming behavior — with a forensics layer in the middle.

---

## What the spec demands (the bar)

From the *LLM Cost Forensics — Complete Build Guide*:

1. **Drop-in proxy** — API-compatible with OpenAI (`/v1/chat/completions`) and
   Anthropic (`/v1/messages`); adoption = one `base_url` swap.
2. **Exact cost accounting** — provider-reported usage first, real tokenizer
   (tiktoken) as streaming fallback; prices in a **version-controlled rate card**
   (`config/rate_card.json`) that hot-reloads without a deploy.
3. **Streaming cost calculation** — chunks pass through to the client in real time;
   the proxy accumulates the stream server-side and logs one cost row after close.
   The client never waits on logging. (The hardest piece; interviewers probe it.)
4. **Cost attribution** — team from the scoped proxy key, feature / prompt-version /
   environment from headers. Attribution is a layer the proxy owns, independent of
   the provider's key model.
5. **Waste detection** — retry storms, exact duplicates, semantic near-duplicates
   (shingle-similarity cache), and over-provisioned models.
6. **Quality-delta evaluation** — every substitution suggestion carries an
   LLM-as-judge verdict (`safe`/`risky`/`unsafe`) or an explicit `unevaluated`.
   Nobody is told to swap models blind.
7. **Multi-tenancy** — scoped `cf_` keys per team, **hashed at rest**; teams see only
   their own data, admin keys see the company view.
8. **Fail open** — if the cost-logging infrastructure dies, the user's LLM call still
   succeeds. Observability never breaks the product it observes. (Tested.)
9. **Dashboard** — CFO view ($/feature trend, waste $, budget alerts) + engineer
   view (models, overhead p50/p99, suggestions).

---

## Tech stack

| Layer | Pick | Why (and the alternative it beat) |
|---|---|---|
| Proxy / gateway | **FastAPI (async)** | Streaming needs async; same ecosystem as tokenizers/eval. (LiteLLM = configuring someone else's gateway; Envoy/Go = overkill.) |
| Token counting | **provider `usage` + tiktoken fallback** | Bill exactly like the provider bills. Char-count approximations discredit every number above them. |
| Rate card | **JSON config, hot-reloaded** | Provider price changes are a one-line PR, no deploy. |
| Event store | **SQLAlchemy → SQLite (dev) / Postgres (prod)** | One `DATABASE_URL` swap. ClickHouse is the at-scale answer — see TRADEOFFS in interviews. |
| Semantic cache | **3-word-shingle Jaccard, per-team scoped** | Zero deps, offline, threshold tuned on a labeled hit/miss set. Upgrade path: embeddings + Redis, same interface. |
| Judge | **Anthropic model via `scripts/judge_run.py`** | Runs on samples, never live traffic; its own cost is negligible vs. what it saves. |
| Dashboard | **Single-file HTML + Chart.js served by the proxy** | The metrics logic is the product. Upgrade path: Next.js + Tremor when it needs to face customers. |
| Auth | **Scoped API keys (sha256 at rest) + admin flag** | Mirrors how real providers issue keys. OAuth2/OIDC is the enterprise path — namedrop, don't build. |
| Deploy | **uvicorn anywhere; Railway/Fly for a public demo** | Time budget goes to the product, not Kubernetes. |

## Architecture

```
Client app A ─┐  base_url swap                            ┌─> OpenAI API
Client app B ─┼────────────► COST FORENSICS PROXY ────────┼─> Anthropic API
Batch jobs  ──┘             (FastAPI, async)              └─> mock provider (demo)
                              │
              ┌───────────────┼──────────────────┐
              │ auth+tagging  │ token+cost calc  │ semantic cache
              │ (cf_ keys)    │ (rate card JSON) │ + retry/dup detector
              └───────────────┴────────┬─────────┘
                                       ▼  one row per call (fail-open)
                            EVENT STORE (SQLite/Postgres)
                                       │
                     aggregation (on read) + budget alerts
                                       ▼
                    DASHBOARD  /dashboard  (CFO + engineer views)
                    suggestions ⟵ quality_deltas ⟵ scripts/judge_run.py
```

**Why this shape:** single ingress point (write every capability once) · event store
as source of truth (dashboard never touches providers) · aggregation off the request
path (a slow rollup can never slow a live call).

## Workflow (how a request flows)

1. Client calls `POST /openai/v1/chat/completions` with `Authorization: Bearer cf_...`
   and `X-Feature`, `X-Prompt-Version` headers.
2. Key → team (401 if unknown). Fingerprint = hash(team, provider, model, prompt).
3. Waste detector checks the fingerprint window → `retry` / `duplicate` flags.
4. Semantic cache lookup (per-team scope). Default `CACHE_MODE=detect`: a hit only
   flags the call as `duplicate` / `semantic_dup` waste. With opt-in `CACHE_MODE=serve`,
   hit + `temperature ≤ 0.3` → serve cached response, log a $0 row with `saved_usd`.
5. Otherwise forward to the provider (server-side key). Streaming: chunks are yielded
   to the client as they arrive and accumulated in parallel.
6. After the response/stream completes: usage extracted (or tokenized), cost computed
   from the rate card, one event row written — **fail-open**, in a try/except.
7. Dashboard reads aggregations: `$/feature`, trend, waste by cause, overhead
   p50/p99, substitution suggestions joined with judge verdicts, budget alerts.

## Repo layout

```
app/
  main.py       FastAPI app: proxy routes + aggregation API + dashboard
  proxy.py      pass-through core, streaming accumulation, fail-open logging
  pricing.py    token counting + cost calc (the numbers that must be exact)
  config.py     env + hot-reloading rate card
  store.py      SQLAlchemy models: events, teams, quality_deltas
  auth.py       scoped team keys, sha256 at rest
  waste.py      fingerprinting + retry/duplicate windows
  semcache.py   per-team semantic cache (shingle Jaccard)
  aggregate.py  rollups, waste report, suggestions, budget alerts
  judge.py      LLM-as-judge quality-delta evaluator
  dashboard.py  single-file Chart.js dashboard
config/rate_card.json   versioned pricing (edit → hot reload)
scripts/                mint_key · mock_provider · simulate · judge_run
tests/                  30 tests incl. fail-open chaos + labeled cache set
```

## Quickstart

Full step-by-step in **[SETUP.md](SETUP.md)**. Keyless demo TL;DR:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
uvicorn scripts.mock_provider:app --port 9100 &          # fake OpenAI+Anthropic
OPENAI_BASE_URL=http://localhost:9100 ANTHROPIC_BASE_URL=http://localhost:9100 \
  uvicorn app.main:app --port 8080 &
python scripts/simulate.py                                # traffic + admin key
open http://localhost:8080/dashboard                      # paste the admin key
pytest tests/ -q                                          # 30 passing
```

## Deliberate simplifications (and when to upgrade)

- Shingle-Jaccard semantic cache → embeddings + Redis when near-dup phrasing gets
  more varied than a threshold can catch.
- On-read SQL aggregation → materialized rollups / ClickHouse at millions of rows/day.
- In-process waste window → Redis window when running multiple proxy replicas.
- Structured logs → OpenTelemetry + Jaeger before demoing the latency story to a
  platform team.
