"""A pricing config change must be picked up without a code deploy."""
import json
import os

from app.config import RateCard


def test_rate_card_hot_reload(tmp_path):
    path = tmp_path / "rates.json"
    path.write_text(json.dumps({"openai": {"gpt-4o": {"input_per_1m": 2.5, "output_per_1m": 10.0}}}))
    card = RateCard(path)
    assert card.get("openai", "gpt-4o")["input_per_1m"] == 2.5

    # provider changes the price; we edit the file — no restart, no redeploy
    path.write_text(json.dumps({"openai": {"gpt-4o": {"input_per_1m": 99.0, "output_per_1m": 10.0}}}))
    os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))
    assert card.get("openai", "gpt-4o")["input_per_1m"] == 99.0
