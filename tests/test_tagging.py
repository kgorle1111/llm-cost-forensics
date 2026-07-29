"""Attribution correctness under concurrent load from multiple teams —
zero cross-contamination allowed."""
from concurrent.futures import ThreadPoolExecutor

from conftest import events

from app.auth import mint_key


def test_concurrent_multi_team_attribution(client):
    keys = {name: mint_key(name) for name in ("alpha", "beta", "gamma")}
    per_team = 15

    def fire(team):
        for i in range(per_team):
            r = client.post(
                "/openai/v1/chat/completions",
                json={"model": "gpt-4o-mini",
                      "messages": [{"role": "user", "content": f"{team} req {i}"}]},
                headers={"Authorization": f"Bearer {keys[team]}",
                         "X-Feature": f"{team}-feature",
                         "X-Prompt-Version": "v2"},
            )
            assert r.status_code == 200

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fire, list(keys) * 1))

    rows = events()
    assert len(rows) == per_team * 3
    for row in rows:
        # every row's feature must match its team — the contamination check
        assert row.feature == f"{row.team}-feature"
        assert row.prompt_version == "v2"
    for team in keys:
        assert sum(1 for r in rows if r.team == team) == per_team
