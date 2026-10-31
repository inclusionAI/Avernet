"""Frozen constants for the workspace density roster.

Every constant pins one mechanically verified fact about the frozen upstream
inputs: SHA-256 digests, line spans, family censuses, ledger decompositions,
plumbing counts and the P0 precondition determination.  The gates in
``gates.py`` and the assembly in ``contract.py`` fail closed with
``RunStateError`` when the live inputs drift away from these frozen values,
so a stale constant can never silently slip into a PASS.
"""
from __future__ import annotations

import re
from pathlib import Path

SCHEMA_VERSION = 1
PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-DENSITY-01"
CONTRACT = "workspace-density-v1"
ORACLE_CONTRACT = "group-workspace-density-v1"
FIELD_ORACLE_CONTRACT = "workspace-field-obligations-v1"
MIGRATION_CONTRACT = "migration-contracts-v1"

RUNNER_ROOT = Path(__file__).resolve().parents[2]
E2E_TEST_ROOT = RUNNER_ROOT.parent / "e2e-test"
GROUP_SOURCE_FILE = "group.sh"
STORIES_SOURCE_FILE = "stories.sh"
GROUP_SH_PATH = E2E_TEST_ROOT / GROUP_SOURCE_FILE
STORIES_SH_PATH = E2E_TEST_ROOT / STORIES_SOURCE_FILE
ORACLE_PATH = RUNNER_ROOT / "group-workspace-density-contract.json"
FIELD_ORACLE_PATH = RUNNER_ROOT / "bcs_e2e" / "workspace_field" / "workspace-field-obligations-contract.json"
MIGRATION_PATH = RUNNER_ROOT / "migration-contracts.json"

GROUP_SH_SHA256 = "2bbb82c417261900265ce39f69aebb01633bb0700c4d7c71c1a2c9d7d9131c1d"
STORIES_SH_SHA256 = "3025c6ee245a2a3b514fba18fe913bb8d1ad42dc37183602521f29c496a4f10f"
ORACLE_SHA256 = "8151f8fb1b5e92934b026fe3d429d583b54fc06127bf4d52f7f32556c8b5152a"
FIELD_ORACLE_SHA256 = "0f95726c7011354b7dfccbc1aaa4b1b9595f54f1c698f7c061288c384b42bbdd"
MIGRATION_SHA256 = "9de6702c25ddec7452a385d230179e8b5eb1e4ddc13095dba68ae0579860f630"

STORY_LINE_START = 408
STORY_LINE_END = 427
TAIL_LINE_START = 429
TAIL_LINE_END = 488
GROUP_SCOPED_FIRST = 39
GROUP_SCOPED_LAST = 313

ATOM_COUNT = 63
ATOM_FAMILY_COUNTS = {"api": 26, "cli": 11, "tail": 26}
LEAF_COUNT = 42
EXCLUSION_COUNT = 21
PARENT_COUNT = 17
PARSED_COUNT = 59
RESIDUAL_COUNT_GAP = 0

LEAF_FAMILY_COUNTS = {"api": 19, "cli": 7, "tail": 16}
EXCLUSION_FAMILY_COUNTS = {"api": 7, "cli": 4, "tail": 10}

COMMITTED_FIELD_TOTAL = 43
COMMITTED_FIELD_BASELINE = 17
COMMITTED_FIELD_ROWS = 26
COMMITTED_COMMON_ATOMS = 24
COMMITTED_ONLY_ATOMS = 2
MINE_ONLY_ATOMS = 18
COMMITTED_FIELD_UNION = 26
DISTINCT_DISCLOSED_ATOMS = 44

ADDRESS_KEY_COUNT = 36
LEAF_ADDRESS_KEY_COUNT = 27
EXCLUSION_ONLY_ADDRESS_KEY_COUNT = 9

PLUMBING_TOTAL = 44
PLUMBING_COUNTS = {
    "narration_info": 16,
    "cli_token_skip_guard": 6,
    "empty_group_id_guard": 6,
    "implicit_cli_get_read": 4,
    "best_effort_cleanup_api_delete": 7,
    "cli_helper_definition": 2,
    "deprecated_label_401_guard": 1,
    "story_tail_bind": 2,
}

LIVENESS_ONLY_TYPES = frozenset({"status_eq", "exit_zero"})
DEPRECATED_WORKSPACE_PARENT = "WORKSPACE.workspace"
DEPRECATED_CLASS = "DEPRECATED_ENDPOINT_GONE_CONTRACT_AT_PARENT_ENVELOPE"
RIDER_CLASS = "SUCCESS_LIVENESS_RIDER_CHARGED_TO_CONTENT_LEAF"

METHOD_VERB = re.compile(r"^(assert_eq|assert_not_empty|assert_json_eq|assert_json_not_empty|require_status|pass|fail)\b")
STORY_CALL = re.compile(r"^    ([A-Za-z_][A-Za-z0-9_]*)(?: \|\| return)?$")
FUNCTION_OPEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{$")

CONSTANT_ENV_FAKE_GROUP = "grp-workspace-density-local"

P0_DETERMINATION = {
    "status": "LANDED",
    "verdict_basis": (
        "The P0 workspace contract precondition is already committed on this branch; "
        "the coverage-map 'P0 pending-landing' marker was snapshot-scoped and is superseded."
    ),
    "evidence": [
        "commit 6114dbfbc (2026-10-30T20:10) landed the frozen 63-atom "
        "group-workspace-density-contract.json oracle and the "
        "bcs_e2e/group_workspace_density.py projector",
        "commit 205715228 (2026-10-31T05:46) landed bcs_e2e/workspace_field/ with the "
        "43-obligation workspace-field-obligations-contract.json and registered "
        "DUAL_WORKSPACE_FIELD_COMMAND 'dual-workspace-field-run' in "
        "bcs_e2e/case_command_registration.py and bcs_e2e/cli.py",
        "executor-migration.json row story_user_operates_group_workspace shows "
        "mode=DUAL and preprod_verdict=PASS with empty gates and notes",
        "coverage-map-20261031 row #7 marker 'P0 pending-landing' predates the "
        "05:46 landing (map snapshot commit 754cab5ed 03:24); gap=42 and "
        "feasibility=3 remain the density delta basis",
    ],
    "blocked_leaves": [],
    "conclusion": "All 42 content atoms are obligation-ized as field leaves; no leaf is classed as a P0-blocked documented gap.",
}

_MAP_SNAPSHOT_ROW_7 = (
    "26绿/45（parsed 59） | PASS 10/10 叶（obl 17） | DUAL | PARTIAL(r8) | "
    "parsed 59 − 义务 17 = 42（快照口径）；在飞契约 43=17+26field 层；P0 落地后残余 16"
)
_MAP_RANK1_BASIS = (
    "密度 delta=42×3；零新物料（r8 9/10 基础）；P0 契约落地后逐字段展开，残余 16"
)
