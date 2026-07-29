"""Run the quality-delta evaluator for one feature.

  python scripts/judge_run.py --feature ticket-summarizer \
      --provider openai --current gpt-4o --candidate gpt-4o-mini --samples 20

Needs: PROMPT_LOGGING=full on the proxy (so prompts were stored) and real
provider keys in .env. Judge model: JUDGE_MODEL env (default claude-sonnet-5).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.judge import evaluate  # noqa: E402
from app.store import init_db  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature", required=True)
    ap.add_argument("--provider", default="openai", choices=["openai", "anthropic"])
    ap.add_argument("--current", required=True, help="model currently in use")
    ap.add_argument("--candidate", required=True, help="cheaper model to evaluate")
    ap.add_argument("--samples", type=int, default=20)
    args = ap.parse_args()
    init_db()
    row = evaluate(args.feature, args.provider, args.current, args.candidate, args.samples)
    print(f"feature={row.feature}  {row.model_a} vs {row.model_b}")
    print(f"samples={row.samples}  win_rate={row.win_rate_b}  delta={row.quality_delta}")
    print(f"VERDICT: {row.verdict}")
