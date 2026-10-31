"""Shared frozen-charter constants and helpers for the agent-network density
test modules.  The original single-file charter (tests/test_agent_network_density.py)
was split into contract/replay/registration_proposal/hygiene modules to satisfy the
repository 1000-line CI budget; every assertion byte is preserved verbatim."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.agent_network_density import (  # noqa: E402
    DENSITY_CONTRACT, DENSITY_OBLIGATION_COUNT, DRIVER_TOKEN_ENV,
    EXECUTOR_ROW_INDEX, HUMAN_COOKIE_ENV, PARSED_OBLIGATION_COUNT, PARENT,
    PARENT_OBLIGATION_COUNT, PROPOSAL_TOKEN, RESIDUAL_COUNT_GAP,
    STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS, density_plan, mock_observations,
    obligations, project, registration_proposal)
from bcs_e2e.agent_network_density.registration_proposal import (  # noqa: E402
    EXPECTED_ROW_FACE, MD_ROW, NAMED_PREPROD_GAP_DETAILS, NOTE, QUEUE_BASIS,
    describe_only_mainline_fragment)
from bcs_e2e.agent_network_density.replay import (  # noqa: E402
    ADJUDICATION_MISMATCH, EXECUTION_LOCAL, EXECUTION_PREPROD,
    EXECUTION_INJECTED, MOCK_BASIS, OBSERVATION_ABSENT, OBSERVATION_MISMATCH,
    OWNED_LOCAL_LEDGER, PREPROD_EXECUTION_NOT_RUN)
from bcs_e2e.dynamic_family import (  # noqa: E402
    dynamic_family_contract, dynamic_families)
from bcs_e2e.local_network_contract import (  # noqa: E402
    CASE_ID, FUNCTIONS, REQUIREMENTS, SUITE, network_source_binding)
from bcs_e2e.local_network_run import local_network_run  # noqa: E402
from bcs_e2e.run_state import RunStateError  # noqa: E402

import bcs_e2e.inventory as inventory_module  # noqa: E402

AN_OBL = importlib.import_module("bcs_e2e.agent_network_density.obligations")
AN_REPLAY = importlib.import_module("bcs_e2e.agent_network_density.replay")
AN_PROPOSAL = importlib.import_module(
    "bcs_e2e.agent_network_density.registration_proposal")

RUNNER_ROOT = Path(AN_OBL.__file__).resolve().parents[2]
E2E_TEST = RUNNER_ROOT.parent / "e2e-test"
DISKS = {"stories.sh": E2E_TEST / "stories.sh",
         "actor.sh": E2E_TEST / "actor.sh", "common.sh": E2E_TEST / "common.sh",
         "e2e.sh": E2E_TEST / "e2e.sh"}
STORY_DISK = DISKS["stories.sh"]
ACTOR_DISK = DISKS["actor.sh"]
COMMON_DISK = DISKS["common.sh"]
RUNNER_DISK = DISKS["e2e.sh"]
CONTRACT_DISK = RUNNER_ROOT / "network-migration-contract.json"
COVERAGE_MAP_DISK = RUNNER_ROOT / "coverage-map" / "coverage-map-20261031.json"
EXECUTOR_MIGRATION_DISK = RUNNER_ROOT / "executor-migration.json"
PACKAGE_DIR = Path(AN_OBL.__file__).resolve().parent
PLATFORM = "NETWORK.platform"
LEG_PASS_LINES = [269, 270, 271, 274, 275, 276, 277, 280]
EXPECTED_SITES = [20, 2, 2, 3, 2, 2, 2, 20]
EXPECTED_CHARGED = [3, 0, 0, 0, 0, 0, 0, 0]
UNREACHED = ("NETWORK.actor-list", "NETWORK.actor-search",
             "NETWORK.actor-status", "NETWORK.bots-my",
             "NETWORK.bots-paged", "NETWORK.bots-query",
             "NETWORK.register-onboard")
LEAF_TABLE = (
    ("AN.F01", AN_OBL.FAIL_ANCHOR_LINE, AN_OBL.SOURCE_FILE,
     "story_require_status", "http_status", 9, "FAIL", None),
    ("AN.F02", AN_OBL.WARN_RESPONSE_LINE, AN_OBL.SOURCE_FILE,
     "story_require_status_warn", "response_body", 10, "WARN",
     AN_OBL.FAIL_ANCHOR_LINE),
    ("AN.F03", AN_OBL.RUNNER_STOP_LINE, AN_OBL.RUNNER_FILE,
     "runner_story_stop_warn", "story_flow", 11, "WARN",
     AN_OBL.FAIL_ANCHOR_LINE),
)

# Guard replica constants are assembled from pieces so this file itself
# never carries the forbidden literals as single string constants.
_BEARER = "Bearer" + " "
_FORBIDDEN_TEXT = (
    "X-Avernet-" + "Principal", "X-E2E-" + "Run-ID",
    "Authorization: " + "Bearer", _BEARER, "curl" + " ", "pkill" + " ",
    "kill" + " -", "DROP " + "TABLE", "TRUNCATE " + "TABLE", "DELETE " + "FROM")
_FORBIDDEN_IMPORTS = {"requests", "httpx", "urllib", "urllib.request",
                      "socket", "subprocess", "sqlite3", "pymysql", "mysql",
                      "psycopg", "shlex"}
_FORBIDDEN_CALLS = {"urlopen", "create_connection", "Popen", "run", "system",
                    "kill", "popen"}


def _guard_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.names[0].name if isinstance(node, ast.Import) else (
                node.module or "")
            if any(module == name or module.startswith(name + ".")
                   for name in _FORBIDDEN_IMPORTS):
                found.append(f"{path}:{node.lineno}: forbidden import {module}")
        elif isinstance(node, ast.Call):
            function = (node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr
                        if isinstance(node.func, ast.Attribute) else "")
            if function in _FORBIDDEN_CALLS:
                found.append(f"{path}:{node.lineno}: forbidden call {function}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for text in _FORBIDDEN_TEXT:
                if text in node.value:
                    found.append(f"{path}:{node.lineno}: forbidden literal")
    return found


def _disk_lines(filename: str) -> list[str]:
    return DISKS[filename].read_text(encoding="utf-8").splitlines()


def _disk_text(filename: str) -> str:
    return DISKS[filename].read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_all(case, fragments, haystack):
    for fragment in fragments:
        case.assertIn(fragment, haystack)


def _unregistered_world() -> ExitStack:
    """Patch the three proposal bindings to the pre-registration face.

    The live world today matches the proposal face (the owner session has
    not landed the note yet); after integration the same patcher restores
    the pre-registration world the describe-only arithmetic is specified
    against, so these assertions stay meaningful in both states.
    """
    statuses = inventory_module.load_executor_migration()
    face = dict(statuses[AN_OBL.STORY_FUNCTION])
    face["notes"] = []
    snapshot = {story: (face if story == AN_OBL.STORY_FUNCTION
                        else dict(existing))
                for story, existing in statuses.items()}
    notes = frozenset(inventory_module.EXECUTOR_MIGRATION_NOTES) - {NOTE}
    counts = {key: value for key, value in
              inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS.items()
              if key != NOTE}
    stack = ExitStack()
    stack.enter_context(mock.patch.object(
        AN_PROPOSAL, "load_executor_migration", return_value=snapshot))
    stack.enter_context(mock.patch.object(
        AN_PROPOSAL, "EXECUTOR_MIGRATION_NOTES", notes))
    stack.enter_context(mock.patch.object(
        AN_PROPOSAL, "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS", counts))
    return stack


