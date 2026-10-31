"""Describe-only mainline integration proposal; shared faces stay untouched.

The authority density slice owns no new command: the landed
``dual-authority-run`` envelope already drives all 42 frozen shell
assertions on the owned-local LIVE leg. Mainline integration adds only this
short token to the executor-migration row notes. The preprod leg stays an
honest NOT_RUN behind the seven named admission gaps; nothing here unfreezes
the remote column, resolves a credential reference, or reviews a route
binding.
"""
from __future__ import annotations

from typing import Any

from .contract import (
    CASE_ID, CONTRACT, PARSED_OBLIGATION_COUNT, RESIDUAL_COUNT_GAP,
    SOURCE_SHA256, SOURCE_FILE,
)
from .defect import PREPROD_GAP_IDS
from .reporting import INTEGRATION_TOKEN

PROPOSAL_STATUS = "DESCRIBE_ONLY"
ENVELOPE_COMMAND = "dual-authority-run"
EXECUTOR_ROW = 18
EXECUTOR_MIGRATION_ROW = 35


def registration_proposal() -> dict[str, Any]:
    return {
        "status": PROPOSAL_STATUS,
        "token": INTEGRATION_TOKEN,
        "executor_row": EXECUTOR_ROW,
        "executor_migration_row": EXECUTOR_MIGRATION_ROW,
        "envelope_command": ENVELOPE_COMMAND,
        "new_registration_command": False,
        "case_id": CASE_ID,
        "contract": CONTRACT,
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_SHA256,
        "parsed_obligations": PARSED_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "preprod_gap_ids": list(PREPROD_GAP_IDS),
        "notes": (
            "Mainline serial integration should add only this short token to "
            "executor row #18 notes (executor-migration.json rows index 35, "
            "story_authority_user_journey); the existing dual-authority-run "
            "envelope carries the 42-obligation shell projection locally and "
            "no new command is registered. Preprod stays NOT_RUN behind the "
            "seven named admission gaps; remote_validation=NOT_RUN; "
            "grants_release_credit=false."),
        "suggested_notes": [
            "AUTHORITY_DENSITY_42_OBLIGATIONS_20261031: 42 shell assertions "
            "obligation-ized in bcs_e2e/authority_density; local replay 42/42 "
            "PASS; residual_count_gap=0",
            "preprod admission diagnosed, not executed: 7 named gaps "
            "AUTHORITY_PREPROD_ADMISSION_*; verdict NOT_RUN; "
            "remote_validation=NOT_RUN; grants_release_credit=false",
        ],
        "red_lines": [
            "executor-migration.json",
            "bcs_e2e/inventory.py",
            "bcs_e2e/cli.py",
            "bcs_e2e/case_command_registration.py",
            "coverage-map/",
            "no write into the authority dual family directory",
            "no preprod execution or credential minting from this proposal",
        ],
    }
