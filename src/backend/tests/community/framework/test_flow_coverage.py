# src/backend/tests/framework/test_flow_coverage.py
"""Tests for the e2e coverage matrix + exempt list (Plan B)."""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep
from tests.community.framework.flow_coverage import (
    covered_modules,
    all_core_modules,
    SINGLEBOX_E2E_EXEMPT,
)


def test_covered_modules_aggregates_covers():
    flows = [
        FlowCase(name="a", covers=["bot_management"], steps=[FlowStep(method="GET", path="/x")]),
        FlowCase(name="b", covers=["devices", "bot_management"], steps=[FlowStep(method="GET", path="/y")]),
    ]
    assert covered_modules(flows) == {"bot_management", "devices"}


def test_all_core_modules_nonempty_and_strings():
    mods = all_core_modules()
    assert mods and all(isinstance(m, str) for m in mods)
    assert "bot_management" in mods  # known core module


def test_exempt_entries_have_reasons():
    # every exempt entry must carry a non-empty reason string
    assert all(isinstance(r, str) and r for r in SINGLEBOX_E2E_EXEMPT.values())
