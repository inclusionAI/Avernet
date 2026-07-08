# src/backend/tests/architecture/test_e2e_module_coverage.py
"""E3 — every core module is either covered by an e2e flow OR explicitly
exempt in SINGLEBOX_E2E_EXEMPT (with a reason). No third state.

Plan C real flows are now injected: skill_center is covered by an actual
e2e flow and has been drained from SINGLEBOX_E2E_EXEMPT. The remaining
modules still rest on the exempt list; they get drained one by one as
real flows are added to cover them.
"""
from __future__ import annotations

import pytest

from tests.community._flows.access.api_lifecycle import ACCESS_LIFECYCLE_FLOWS
from tests.community._flows.bot_chat.api_lifecycle import BOT_CHAT_LIFECYCLE_FLOWS
from tests.community._flows.bot_collaborator.api_lifecycle import BOT_COLLABORATOR_LIFECYCLE_FLOWS
from tests.community._flows.bot_management.api_lifecycle import BOT_MANAGEMENT_LIFECYCLE_FLOWS
from tests.community._flows.cron.api_lifecycle import CRON_FLOWS
from tests.community._flows.devices.api_lifecycle import DEVICES_LIFECYCLE_FLOWS
from tests.community._flows.expert_chat.api_lifecycle import EXPERT_CHAT_FLOWS
from tests.community._flows.harness.api_lifecycle import HARNESS_LIFECYCLE_FLOWS
from tests.community._flows.mcp.api_lifecycle import MCP_FLOWS
from tests.community._flows.quality.api_lifecycle import QUALITY_FLOWS
from tests.community._flows.resources.api_lifecycle import RESOURCES_LIFECYCLE_FLOWS
from tests.community._flows.skill_center.api_lifecycle import API_LIFECYCLE_FLOWS
from tests.community.framework.flow_coverage import (
    SINGLEBOX_E2E_EXEMPT,
    all_core_modules,
    covered_modules,
)


# Plan C appends real flows here (or swaps for a registry). 新模块的流 import
# 进来 extend 此列表。
REGISTERED_FLOWS: list = [
    *API_LIFECYCLE_FLOWS,
    *ACCESS_LIFECYCLE_FLOWS,
    *BOT_CHAT_LIFECYCLE_FLOWS,
    *RESOURCES_LIFECYCLE_FLOWS,
    *DEVICES_LIFECYCLE_FLOWS,
    *BOT_MANAGEMENT_LIFECYCLE_FLOWS,
    *HARNESS_LIFECYCLE_FLOWS,
    *BOT_COLLABORATOR_LIFECYCLE_FLOWS,
    *MCP_FLOWS,
    *EXPERT_CHAT_FLOWS,
    *CRON_FLOWS,
    *QUALITY_FLOWS,
]


def test_every_core_module_covered_or_exempt():
    covered = covered_modules(REGISTERED_FLOWS)
    exempt = set(SINGLEBOX_E2E_EXEMPT)
    core = all_core_modules()

    uncovered = sorted(m for m in core if m not in covered and m not in exempt)
    if uncovered:
        pytest.fail(
            "E3 violation: these core modules are neither covered by an e2e "
            "flow nor in SINGLEBOX_E2E_EXEMPT:\n" + "\n".join(uncovered)
        )


def test_exempt_list_has_no_stale_entries():
    # an exempt entry that isn't a real core module is a bookkeeping rot
    core = all_core_modules()
    stale = sorted(m for m in SINGLEBOX_E2E_EXEMPT if m not in core)
    if stale:
        pytest.fail(
            "SINGLEBOX_E2E_EXEMPT has entries that aren't real core modules "
            "(rename/removed?):\n" + "\n".join(stale)
        )
