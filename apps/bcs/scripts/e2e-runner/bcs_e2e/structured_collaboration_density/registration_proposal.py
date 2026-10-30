"""Describe-only mainline integration proposal; no shared-face writes."""
from __future__ import annotations

from .contract import CONTRACT, PARSED_OBLIGATION_COUNT, RESIDUAL_COUNT_GAP, SOURCE_SHA256
from .reporting import INTEGRATION_TOKEN

PROPOSAL_STATUS = "DESCRIBE_ONLY"
ENVELOPE_COMMAND = "dual-structured-density-run"


def registration_proposal() -> dict[str, object]:
    return {
        "status": PROPOSAL_STATUS,
        "token": INTEGRATION_TOKEN,
        "executor_row": 3,
        "envelope_command": ENVELOPE_COMMAND,
        "new_registration_command": False,
        "contract": CONTRACT,
        "source_sha256": SOURCE_SHA256,
        "parsed_obligations": PARSED_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "notes": "Mainline serial integration should add only this token to executor row #3 notes; no executor registration command is proposed.",
        "red_lines": ["executor-migration.json", "bcs_e2e/inventory.py", "bcs_e2e/cli.py", "bcs_e2e/case_command_registration.py"],
    }
