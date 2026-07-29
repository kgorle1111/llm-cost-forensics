"""Mint a scoped proxy key for a team. The plaintext is printed ONCE.

  python scripts/mint_key.py checkout --budget 500
  python scripts/mint_key.py platform --admin
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import mint_key  # noqa: E402
from app.store import init_db  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("team")
    ap.add_argument("--budget", type=float, default=0.0, help="monthly budget USD (0 = no alerting)")
    ap.add_argument("--admin", action="store_true", help="admin keys see all teams in the dashboard")
    args = ap.parse_args()
    init_db()
    key = mint_key(args.team, args.budget, args.admin)
    print(f"team={args.team} admin={args.admin} budget=${args.budget}/mo")
    print(f"key (save it now, only the hash is stored): {key}")
