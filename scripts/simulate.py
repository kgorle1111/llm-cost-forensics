"""Synthetic traffic generator — the keyless demo.

Run the demo stack (mock provider + proxy pointed at it), then this script.
It mints three teams, replays a realistic week of traffic including a retry
storm, duplicate calls, and an over-provisioned model, then prints the
summary — so the dashboard has a story to tell with zero API keys.

  Terminal 1: uvicorn scripts.mock_provider:app --port 9100
  Terminal 2: OPENAI_BASE_URL=http://localhost:9100 ANTHROPIC_BASE_URL=http://localhost:9100 \
              uvicorn app.main:app --port 8080
  Terminal 3: python scripts/simulate.py
"""
import json
import random
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import mint_key  # noqa: E402
from app.store import init_db  # noqa: E402

PROXY = "http://localhost:8080"
random.seed(7)

TICKETS = [
    "Customer says checkout page freezes on the payment step",
    "User cannot reset password, email never arrives",
    "Refund requested for duplicate charge on order 5512",
    "App crashes when uploading a profile photo on Android",
    "Subscription renewed at the wrong price tier",
]


def call(key: str, provider: str, model: str, prompt: str, feature: str,
         stream: bool = False, version: str = "v1"):
    if provider == "openai":
        url, body = f"{PROXY}/openai/v1/chat/completions", {
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": stream, "temperature": 0.0}
    else:
        url, body = f"{PROXY}/anthropic/v1/messages", {
            "model": model, "max_tokens": 256, "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}], "stream": stream}
    headers = {"Authorization": f"Bearer {key}", "X-Feature": feature,
               "X-Prompt-Version": version}
    r = httpx.post(url, json=body, headers=headers, timeout=30)
    if stream:
        r.read()
    return r


def main():
    init_db()
    keys = {
        "checkout": mint_key("checkout", monthly_budget_usd=5.0),
        "support": mint_key("support", monthly_budget_usd=50.0),
        "search": mint_key("search", monthly_budget_usd=20.0),
    }
    admin = mint_key("platform-admin", is_admin=True)
    print("teams minted. ADMIN KEY for the dashboard (save it):")
    print(f"  {admin}\n")

    # 1) normal support traffic — cheap model, all fine
    for t in TICKETS * 4:
        call(keys["support"], "openai", "gpt-4o-mini",
             f"Summarize this ticket: {t} [{random.randint(0, 9999)}]", "ticket-summarizer")

    # 2) checkout team using an expensive model for a trivial task (over-provisioned)
    for i in range(12):
        call(keys["checkout"], "anthropic", "claude-opus-4-8",
             f"Classify sentiment as positive/negative: 'order {i} arrived'",
             "review-sentiment")

    # 3a) a hard failure hammered repeatedly (all attempts error)
    for _ in range(3):
        call(keys["checkout"], "openai", "gpt-4o",
             "FAIL Summarize the checkout funnel report", "checkout-summarizer")

    # 3b) a transient failure + a client retry loop: attempts AFTER the error
    #     succeed and cost real money — that spend is flagged as retry waste
    for _ in range(4):
        call(keys["checkout"], "openai", "gpt-4o",
             "FLAKY Summarize the checkout funnel report", "checkout-summarizer")

    # 3c) creative feature re-sending the same high-temperature prompt: cache
    #     must NOT serve it (temp too high), but the duplicates are still waste
    for _ in range(3):
        r = httpx.post(f"{PROXY}/openai/v1/chat/completions",
                       json={"model": "gpt-4o", "temperature": 0.9,
                             "messages": [{"role": "user",
                                           "content": "Write a creative tagline for running shoes"}]},
                       headers={"Authorization": f"Bearer {keys['checkout']}",
                                "X-Feature": "tagline-gen"}, timeout=30)

    # 4) duplicate calls a cache should have served (identical, temperature 0)
    for _ in range(5):
        call(keys["search"], "openai", "gpt-4o",
             "Rewrite this search query for better recall: red running shoes",
             "query-rewriter")

    # 5) near-duplicate (semantic dup, different whitespace/case)
    call(keys["search"], "openai", "gpt-4o",
         "rewrite this search query for better recall:  RED running shoes",
         "query-rewriter")

    # 6) streaming traffic
    for t in TICKETS:
        call(keys["support"], "anthropic", "claude-haiku-4-5",
             f"Draft a reply: {t}", "reply-drafter", stream=True)

    summary = httpx.get(f"{PROXY}/api/summary",
                        headers={"Authorization": f"Bearer {admin}"}).json()
    print("proxy summary:")
    print(json.dumps(summary, indent=2))
    print(f"\nopen {PROXY}/dashboard and paste the admin key above.")


if __name__ == "__main__":
    main()
