"""THE production-judgment test: if cost-logging infrastructure dies, the
user's LLM call still succeeds. Observability must never break the product
it observes."""
from app import proxy


def _body():
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "still works?"}]}


def test_dead_event_store_does_not_block_llm_calls(client, team_key, monkeypatch):
    def boom(**kw):
        raise RuntimeError("database is down")
    monkeypatch.setattr(proxy, "log_event", boom)

    r = client.post("/openai/v1/chat/completions", json=_body(),
                    headers={"Authorization": f"Bearer {team_key}"})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello world"


def test_broken_waste_detector_does_not_block(client, team_key, monkeypatch):
    monkeypatch.setattr(proxy.detector, "classify",
                        lambda fp: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/openai/v1/chat/completions", json=_body(),
                    headers={"Authorization": f"Bearer {team_key}"})
    assert r.status_code == 200


def test_broken_cache_does_not_block(client, team_key, monkeypatch):
    monkeypatch.setattr(proxy.cache, "lookup",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("redis gone")))
    r = client.post("/openai/v1/chat/completions",
                    json={**_body(), "temperature": 0.0},
                    headers={"Authorization": f"Bearer {team_key}"})
    assert r.status_code == 200
