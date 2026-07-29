"""Pass-through fidelity + cost logging, non-streaming and streaming."""
import json

from conftest import events


def _openai_body(prompt="Summarize the quarterly report", **kw):
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}], **kw}


def test_nonstream_passthrough_and_cost(client, team_key):
    r = client.post("/openai/v1/chat/completions", json=_openai_body(),
                    headers={"Authorization": f"Bearer {team_key}",
                             "X-Feature": "report-summarizer"})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello world"

    e = events()[-1]
    assert e.team == "test-team"
    assert e.feature == "report-summarizer"
    assert (e.input_tokens, e.output_tokens) == (100, 50)
    # gpt-4o: 100 * 2.5/1M + 50 * 10/1M
    assert abs(e.cost_usd - (100 * 2.5 + 50 * 10) / 1_000_000) < 1e-9
    assert r.headers["x-proxy-cost-usd"] != ""


def test_streaming_chunks_and_final_cost_row(client, team_key):
    with client.stream("POST", "/v1/chat/completions",
                       json=_openai_body(stream=True),
                       headers={"Authorization": f"Bearer {team_key}"}) as r:
        text = "".join(chunk for chunk in r.iter_text())
    assert "Hello " in text and "[DONE]" in text  # client saw the raw SSE

    e = events()[-1]
    assert e.stream is True
    assert (e.input_tokens, e.output_tokens) == (100, 50)  # from stream usage block
    assert e.cost_usd is not None


def test_anthropic_route_and_stream_usage(client, team_key):
    r = client.post("/anthropic/v1/messages",
                    json={"model": "claude-haiku-4-5", "max_tokens": 100,
                          "messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": f"Bearer {team_key}"})
    assert r.status_code == 200
    e = events()[-1]
    assert e.provider == "anthropic"
    assert (e.input_tokens, e.output_tokens) == (80, 40)
    assert abs(e.cost_usd - (80 * 1 + 40 * 5) / 1_000_000) < 1e-9

    with client.stream("POST", "/anthropic/v1/messages",
                       json={"model": "claude-haiku-4-5", "max_tokens": 100, "stream": True,
                             "messages": [{"role": "user", "content": "hi stream"}]},
                       headers={"Authorization": f"Bearer {team_key}"}) as r:
        r.read()
    e = events()[-1]
    assert e.stream is True and (e.input_tokens, e.output_tokens) == (80, 40)


def test_provider_error_passes_through_and_logs(client, team_key):
    r = client.post("/openai/v1/chat/completions", json=_openai_body("FAIL now"),
                    headers={"Authorization": f"Bearer {team_key}"})
    assert r.status_code == 500
    assert json.loads(r.content)["error"]["message"] == "upstream boom"
    assert events()[-1].error is True


def test_unknown_key_rejected(client):
    r = client.post("/openai/v1/chat/completions", json=_openai_body(),
                    headers={"Authorization": "Bearer cf_deadbeef"})
    assert r.status_code == 401
