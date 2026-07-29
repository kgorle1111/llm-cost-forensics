# SETUP — Up and Running

Two ways to run: **keyless demo** (5 minutes, zero API keys, mock provider) and
**real mode** (your OpenAI/Anthropic keys, real spend tracked).

---

## 0. Prerequisites

- Python 3.11+ (`python3 --version`)
- Nothing else. SQLite is the default store; no Docker, Postgres, or Redis needed
  to start.

## 1. Install

```bash
cd build2-llm-cost-forensics
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 2. Keyless demo (do this first)

Three terminals (all with `source .venv/bin/activate`):

```bash
# T1 — mock OpenAI+Anthropic provider
uvicorn scripts.mock_provider:app --port 9100

# T2 — the proxy, pointed at the mock
OPENAI_BASE_URL=http://localhost:9100 ANTHROPIC_BASE_URL=http://localhost:9100 \
  uvicorn app.main:app --port 8080

# T3 — synthetic week of traffic: retry storm, duplicates, over-provisioned model
python scripts/simulate.py
```

`simulate.py` prints an **admin key** (`cf_...`). Open
<http://localhost:8080/dashboard>, paste it top-right, click **Load**. You should
see: spend by feature, waste by cause (retries in `checkout-summarizer`), cache
savings, and a suggestion to swap `claude-opus-4-8 → claude-sonnet-5` on
`review-sentiment`.

## 3. Real mode — API keys

Edit `.env`:

| Key | Where to get it | Used for |
|---|---|---|
| `OPENAI_API_KEY` | <https://platform.openai.com/api-keys> → "Create new secret key" | forwarding `/openai/v1/chat/completions` upstream |
| `ANTHROPIC_API_KEY` | <https://console.anthropic.com/settings/keys> → "Create Key" | forwarding `/anthropic/v1/messages` upstream + the judge |

Remove the `*_BASE_URL` overrides (or comment them out) so the proxy talks to the
real APIs, then:

```bash
uvicorn app.main:app --port 8080
```

> Provider keys live **server-side only**. Callers authenticate to the proxy with
> team keys — they never see or hold your OpenAI/Anthropic credentials.

## 4. Mint team keys

```bash
python scripts/mint_key.py checkout --budget 500      # $500/mo budget alerting
python scripts/mint_key.py support  --budget 1000
python scripts/mint_key.py platform-admin --admin     # sees all teams in dashboard
```

Each key is printed **once**; only its sha256 hash is stored.

## 5. Point an app at the proxy (the one-line adoption)

```python
# OpenAI SDK
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/openai/v1",
                api_key="cf_YOUR_TEAM_KEY",
                default_headers={"X-Feature": "ticket-summarizer",
                                 "X-Prompt-Version": "v3"})

# Anthropic SDK
from anthropic import Anthropic
client = Anthropic(base_url="http://localhost:8080/anthropic",
                   api_key="cf_YOUR_TEAM_KEY",
                   default_headers={"X-Feature": "reply-drafter"})
```

Streaming, temperature, every parameter — unchanged. The response is byte-identical
to the provider's, plus an `x-proxy-cost-usd` header.

## 6. Run the quality-delta judge

Substitution suggestions show `unevaluated` until you produce evidence:

```bash
# 1) allow prompt storage (required to replay real traffic)
echo "PROMPT_LOGGING=full" >> .env   # restart the proxy after
# 2) collect some traffic, then:
python scripts/judge_run.py --feature ticket-summarizer \
    --provider openai --current gpt-4o --candidate gpt-4o-mini --samples 20
```

The verdict (`safe`/`risky`/`unsafe`, win rate, mean delta) is stored and appears
on the suggestion row in the dashboard.

## 7. Tests

```bash
pytest tests/ -q        # 28 tests, no network, no paid API calls
```

Covers: hand-computed cost values, rate-card hot reload, byte-identical
pass-through, streaming cost rows, concurrent multi-team attribution with zero
cross-contamination, retry/duplicate golden set, labeled cache hit/miss pairs,
cross-team cache isolation, and the fail-open chaos tests.

## 8. Production notes

- **Postgres:** `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/costforensics`
  (add `psycopg[binary]` to requirements). Schema auto-creates.
- **Price change?** Edit `config/rate_card.json`. Hot-reloads on the next request.
- **Privacy:** default `PROMPT_LOGGING=redacted` stores no prompt text — only
  fingerprints and token counts. `full` is opt-in, for the judge.
- **Public demo:** deploy to Railway/Fly (uvicorn + the SQLite file, or a managed
  Postgres). One service is enough.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `401 missing or malformed proxy key` | Caller must send `Authorization: Bearer cf_...` (a minted team key, not a provider key) |
| Dashboard empty | Paste a valid key top-right; team keys only see their own team's rows |
| `unpriced_calls > 0` in summary | The model isn't in `config/rate_card.json` — add it |
| Cache never hits | Serving requires `temperature ≤ 0.3`; higher temps are flagged as waste but not served |
| Judge: "no stored prompts" | Set `PROMPT_LOGGING=full`, restart, send traffic first |
