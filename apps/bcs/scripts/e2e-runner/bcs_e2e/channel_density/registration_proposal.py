"""Describe-only mainline integration proposal for the Wave-C channel density backfill."""
from __future__ import annotations

from .contract import (
    CHANNEL_BINDINGS_PATH_FACE_DRIFT_ID,
    CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID,
    CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID,
    CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID,
    DENSITY_CONTRACT,
    DENSITY_OBLIGATION_COUNT,
    LEGACY_CLI_REQUIREMENTS,
    LEGACY_DUAL_COMMANDS,
    LEGACY_HTTP_REQUIREMENTS,
    PARSED_OBLIGATION_COUNT,
    PARENT_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP,
    SOURCE_REVISION,
    STORY_CLI,
    STORY_HTTP,
    channel_density_plan,
)

PROPOSAL_TOKEN = "CHANNEL_DENSITY_14_OBLIGATIONS_20261031"

_NOTE_HTTP = (
    "row #10 story_user_validates_external_channel_setup: register 7 source-anchored density "
    "obligations under the retained CHANNEL.* parent envelope (13 parsed = 6 legacy parents + 7 "
    "atoms; residual_count_gap=0); notes-only integration, the dual-channel-bindings-run envelope "
    "and its r8 conclusions stay untouched"
)

_NOTE_CLI = (
    "row #11 story_cli_operator_validates_channel_management: register 7 source-anchored density "
    "obligations under the retained CHANNEL-CLI.* parent envelope (16 parsed = 9 legacy parents + 7 "
    "atoms; residual_count_gap=0); preprod keeps BLOCKED_INVENTORIED with the disabled-bridge "
    "divergence registered as a true business judgment (not a defect) and the sim DingTalk design "
    "red disclosed; remote_validation=NOT_RUN and grants_release_credit=false"
)


def registration_proposal() -> dict:
    """Return the describe-only integration proposal; nothing is executed or written."""
    plan = channel_density_plan()
    return {
        "status": "DESCRIBE_ONLY",
        "token": PROPOSAL_TOKEN,
        "contract": DENSITY_CONTRACT,
        "parent_stories": [STORY_HTTP, STORY_CLI],
        "parsed_obligations": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "source_revision": SOURCE_REVISION,
        "reused_dual_channel_envelopes": {
            STORY_HTTP: {
                "existing_executor_row": 10,
                "retained_mode": "DUAL",
                "retained_preprod_verdict": "PASS",
                "reused_command": LEGACY_DUAL_COMMANDS["http"],
                "reused_legacy_requirement_ids": list(LEGACY_HTTP_REQUIREMENTS),
                "reused_case_ids": plan["stories"][STORY_HTTP]["legacy_envelope"]["reused_case_ids"],
            },
            STORY_CLI: {
                "existing_executor_row": 11,
                "retained_mode": "DUAL",
                "retained_preprod_verdict": "BLOCKED_INVENTORIED",
                "reused_command": LEGACY_DUAL_COMMANDS["cli"],
                "reused_legacy_requirement_ids": list(LEGACY_CLI_REQUIREMENTS),
                "reused_case_ids": plan["stories"][STORY_CLI]["legacy_envelope"]["reused_case_ids"],
            },
        },
        "named_registrations": [
            CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID,
            CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID,
            CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID,
            CHANNEL_BINDINGS_PATH_FACE_DRIFT_ID,
        ],
        "notes": [_NOTE_HTTP, _NOTE_CLI],
        "new_registration_command": False,
        "red_lines": [
            "describe-only: no executor-migration.json row is created or rewritten",
            "no edits to inventory.py, cli.py, case_command_registration.py, or artifact.py",
            "no edits to coverage-map/*, workspace-migration-contract.json, or the frozen channel "
            "legacy contracts and family files",
            "preprod stays NOT_RUN with remote_validation=NOT_RUN and grants_release_credit=false",
            "SHA-256 froze both upstream stories; any drift fails closed before the plan is usable",
        ],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def describe_only_mainline_fragment() -> str:
    """Return the text a mainline integrator may apply; this module writes nothing."""
    proposal = registration_proposal()
    return (
        f"{PROPOSAL_TOKEN}: {DENSITY_CONTRACT} registers {PARENT_OBLIGATION_COUNT} reused legacy "
        f"dual-channel parent envelopes plus {DENSITY_OBLIGATION_COUNT} source-anchored density "
        f"obligations across rows #10 and #11 ({PARSED_OBLIGATION_COUNT} parsed, "
        f"residual_count_gap={RESIDUAL_COUNT_GAP}); executor-migration.json notes-only increment "
        f"{proposal['notes'][0]}\n{proposal['notes'][1]}\n"
        "New registration command: none (dual-channel-bindings-run / dual-channel-run envelopes "
        "reused); named registrations: "
        f"{', '.join(proposal['named_registrations'])}; write domain is "
        "bcs_e2e/channel_density/** and tests/test_channel_density.py only."
    )
