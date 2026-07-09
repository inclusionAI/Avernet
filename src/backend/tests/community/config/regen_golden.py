"""Regenerate the golden effective-config snapshots (OSS-0 #3).

Run ONLY when a deliberate effective-config change is made (the golden is a
behavior-preservation gate — regenerating hides drift). Usage::

    DEPLOY_PROFILE=test .venv/bin/python -m tests.community.config.regen_golden
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.community.config.effective_config import PROFILE_PAIRS, compute_effective_config

GOLDEN_DIR = Path(__file__).parent / "golden"


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for profile, (base, overlay) in sorted(PROFILE_PAIRS.items()):
        snapshot = compute_effective_config(base, overlay)
        out = GOLDEN_DIR / f"{profile}.json"
        out.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
