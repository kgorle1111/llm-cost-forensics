"""Golden waste-detection set: known retry storms and known duplicates get
flagged; legitimate distinct calls do NOT (false positives are how a tool
loses trust)."""
from conftest import events


def _body(prompt, **kw):
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}], **kw}


def _h(key, feature="wastey"):
    return {"Authorization": f"Bearer {key}", "X-Feature": feature}


def test_retry_storm_flagged(client, team_key):
    for _ in range(3):
        client.post("/openai/v1/chat/completions",
                    json=_body("FAIL summarize the funnel"), headers=_h(team_key))
    rows = [e for e in events() if e.error]
    assert len(rows) == 3
    assert rows[0].waste_flags == []          # first attempt is not waste
    assert "retry" in rows[1].waste_flags      # hammering after an error is
    assert "retry" in rows[2].waste_flags


def test_exact_duplicate_flagged_and_served_from_cache(client, team_key):
    body = _body("Rewrite: red running shoes", temperature=0.0)
    r1 = client.post("/openai/v1/chat/completions", json=body, headers=_h(team_key))
    r2 = client.post("/openai/v1/chat/completions", json=body, headers=_h(team_key))
    assert r1.status_code == r2.status_code == 200
    assert r2.headers.get("x-proxy-cache") == "hit"
    assert r2.json() == r1.json()

    first, second = [e for e in events()][-2:]
    assert first.waste_flags == []
    assert "duplicate" in second.waste_flags
    assert second.cache_hit is True and second.cost_usd == 0.0
    assert second.saved_usd == first.cost_usd  # the money the cache saved


def test_near_duplicate_semantic_flag(client, team_key):
    client.post("/openai/v1/chat/completions",
                json=_body("Summarize this ticket: checkout page freezes on payment step",
                           temperature=0.0), headers=_h(team_key))
    client.post("/openai/v1/chat/completions",
                json=_body("summarize this ticket:  Checkout page freezes on payment step!",
                           temperature=0.0), headers=_h(team_key))
    assert "semantic_dup" in events()[-1].waste_flags


def test_high_temperature_near_dup_is_flagged_not_served(client, team_key):
    body = _body("Write a creative tagline for a coffee brand", temperature=0.9)
    client.post("/openai/v1/chat/completions", json=body, headers=_h(team_key))
    r2 = client.post("/openai/v1/chat/completions", json=body, headers=_h(team_key))
    # creative call: never serve a stale cached answer, but still count the waste
    assert r2.headers.get("x-proxy-cache") is None
    assert "duplicate" in events()[-1].waste_flags
    assert events()[-1].cache_hit is False


def test_legitimate_distinct_calls_not_flagged(client, team_key):
    for i in range(5):
        client.post("/openai/v1/chat/completions",
                    json=_body(f"Summarize ticket #{i}: totally different issue {i}"),
                    headers=_h(team_key))
    for e in events():
        assert e.waste_flags == []


def test_cross_team_cache_isolation(client):
    """One team's cached responses must never be served to another team."""
    from app.auth import mint_key
    k1, k2 = mint_key("iso-one"), mint_key("iso-two")
    body = _body("Shared question between teams", temperature=0.0)
    client.post("/openai/v1/chat/completions", json=body, headers=_h(k1))
    r = client.post("/openai/v1/chat/completions", json=body, headers=_h(k2))
    assert r.headers.get("x-proxy-cache") is None  # different team => no hit
