"""Central config: env vars + the version-controlled rate card.

The rate card lives in config/rate_card.json (never hardcoded) so a provider
price change is a one-line edit, picked up without a code deploy.
"""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
RATE_CARD_PATH = Path(os.getenv("RATE_CARD_PATH", ROOT / "config" / "rate_card.json"))

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'events.db'}")

# Upstream providers. Point these at scripts/mock_provider.py for a keyless demo.
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# serve = return cached response for near-dup low-temperature requests
# detect = only flag the waste, never change behavior
# off    = disable semantic cache entirely
CACHE_MODE = os.getenv("CACHE_MODE", "serve")
CACHE_TTL_S = float(os.getenv("CACHE_TTL_S", "300"))
CACHE_SIMILARITY = float(os.getenv("CACHE_SIMILARITY", "0.85"))
CACHE_MAX_TEMPERATURE = float(os.getenv("CACHE_MAX_TEMPERATURE", "0.3"))

RETRY_WINDOW_S = float(os.getenv("RETRY_WINDOW_S", "60"))
DUP_WINDOW_S = float(os.getenv("DUP_WINDOW_S", "300"))

# redacted = store only fingerprints + char counts (default, security posture)
# full     = store prompt/response text (required for the quality-delta judge)
PROMPT_LOGGING = os.getenv("PROMPT_LOGGING", "redacted")

JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-sonnet-5")


class RateCard:
    """Hot-reloading rate card: re-reads the JSON file when its mtime changes."""

    def __init__(self, path: Path = RATE_CARD_PATH):
        self.path = Path(path)
        self._mtime = 0.0
        self._card: dict = {}
        self._load()

    def _load(self):
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._card = json.loads(self.path.read_text())
            self._mtime = mtime

    def get(self, provider: str, model: str) -> dict | None:
        self._load()
        models = self._card.get(provider, {})
        if model in models:
            return models[model]
        # prefix match handles dated snapshots e.g. gpt-4o-2024-08-06
        for name, entry in sorted(models.items(), key=lambda kv: -len(kv[0])):
            if model.startswith(name):
                return entry
        return None

    def all(self) -> dict:
        self._load()
        return self._card


rate_card = RateCard()
