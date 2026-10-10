"""Stored XSS: an X-Feature header is caller-controlled, stored, and rendered by
the dashboard. Runs the real dashboard script in node against a stub DOM fed
with the real /api responses, then checks no payload survives as markup."""
import json
import re
import shutil
import subprocess

import pytest

from app.dashboard import DASHBOARD_HTML

PAYLOAD = "<img src=x onerror=alert(1)>"
ENDPOINTS = ["/api/summary", "/api/trend", "/api/by/feature", "/api/by/team",
             "/api/waste", "/api/suggestions", "/api/alerts"]

STUB = """
const responses = %s;
const els = {};
globalThis.document = {getElementById: id => (els[id] ??= {id, innerHTML: '', value: ''})};
globalThis.localStorage = {getItem: () => 'cf_test', setItem: () => {}};
globalThis.fetch = async path => ({ok: true, json: async () => responses[path]});
globalThis.Chart = class { destroy() {} };
%s
setTimeout(() => console.log(JSON.stringify(Object.fromEntries(
  Object.entries(els).map(([k, v]) => [k, v.innerHTML])))), 50);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_untrusted_values_are_escaped_in_dashboard(client, team_key):
    h = {"Authorization": f"Bearer {team_key}", "X-Feature": PAYLOAD}
    body = {"model": "gpt-4o", "temperature": 0.0,
            "messages": [{"role": "user", "content": "same prompt twice"}]}
    for _ in range(2):  # second call is a duplicate -> lands in /api/waste
        client.post("/openai/v1/chat/completions", json=body, headers=h)

    responses = {p: client.get(p, headers={"Authorization": f"Bearer {team_key}"}).json()
                 for p in ENDPOINTS}
    assert any(w["feature"] == PAYLOAD for w in responses["/api/waste"])  # sink is reachable
    # aggregation may not emit these for one call; fake rows so every sink is exercised
    responses["/api/alerts"] = [{"team": PAYLOAD, "pct": 90, "budget_usd": 10,
                                 "mtd_usd": 9, "level": PAYLOAD}]
    responses["/api/suggestions"] = [{
        "feature": PAYLOAD, "current_model": PAYLOAD, "suggested_model": PAYLOAD,
        "current_cost_usd": 1, "projected_cost_usd": 0.1, "monthly_savings_usd": 0.9,
        "quality": {"verdict": PAYLOAD, "win_rate": 0.5, "delta": PAYLOAD, "samples": 3}}]

    script = re.search(r"<script>\n(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    out = subprocess.run(["node", "-e", STUB % (json.dumps(responses), script)],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    html = json.loads(out)
    rendered = html["waste"] + html["alerts"] + html["suggestions"] + html["kpis"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in html["waste"]
    assert "<img" not in rendered
