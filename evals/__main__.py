"""`python -m evals` 跑完整 eval；加 --live 用真實模型（需要 API 金鑰）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

from evals.runner import main  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="跑 Agent Evals")
    ap.add_argument("--live", action="store_true", help="用真實 NVIDIA 模型（需要 NVIDIA_API_KEY）")
    args = ap.parse_args()
    raise SystemExit(main(live=args.live))
