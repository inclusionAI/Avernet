"""Golden effective-config snapshot — the OSS-0 #3 behavior-preservation gate.

For each deploy-profile ``(base, overlay)`` pair, the effective configuration
(raw merged ``user_config`` + every neutral typed config) must equal a committed
golden snapshot. The goldens were generated from the **pre-refactor** tree; any
later commit that relocates a corp value between config files without preserving
the effective result turns the matching profile red — pinpointing exactly which
block drifted.

Regenerate intentionally (only when a *deliberate* effective-config change is
made) with::

    DEPLOY_PROFILE=test .venv/bin/python -m tests.community.config.regen_golden

See ``plan.md`` (Group A) for why this is the primary safety net: corp prod/sim
cannot boot in this worktree, so behavior preservation is proven structurally.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.community.config.effective_config import PROFILE_PAIRS, compute_effective_config

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.mark.parametrize("profile", sorted(PROFILE_PAIRS))
def test_effective_config_matches_golden(profile: str) -> None:
    base, overlay = PROFILE_PAIRS[profile]
    golden_path = GOLDEN_DIR / f"{profile}.json"
    assert golden_path.exists(), (
        f"missing golden {golden_path}; regenerate with "
        f"`python -m tests.community.config.regen_golden`"
    )
    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    actual = compute_effective_config(base, overlay)
    assert actual == expected, (
        f"effective config for profile {profile!r} drifted from golden.\n"
        f"A corp value was relocated without preserving the merged result. "
        f"If this change is intentional, regenerate the golden."
    )
