"""Frozen source face for the operator CLI density wave (executor row #9).

Offline density backfill for story_operator_coordinates_with_cli: the 13
retained OPERATOR-CLI.* contract parents of operator-cli-migration-contract.json
plus 15 source-anchored density leaves (OPCLI.F01..OPCLI.F15) under the
OPERATOR-CLI.* envelope (28 parsed = 13 legacy parents + 15 atoms;
residual_count_gap=0).  The upstream story is a shell harness story, so this
wave freezes (file, line_start, line_end) spans plus per-site metadata instead
of pasting story bytes; bcs_e2e.operator_cli_density.density rebuilds every
parent call line and every atom quote verbatim from the frozen disk bytes at
obligation time and fails closed on any drift.

The package is credential-free and offline: it opens no transport, starts no
process, resolves no credential, and mutates no business resource.  The faces
below mirror the dual-operator-cli-run envelope facts already committed by
bcs_e2e.operator_cli_case_run, bcs_e2e.remote_operator_cli_adapter and
bcs_e2e.operator_cli_runner.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..local_operator_cli_contract import (
    CASE_ID, COMMANDS, HELPERS, PARENT, REQUIREMENTS, SUITE,
    operator_cli_source_binding)
from ..run_state import RunStateError

SCHEMA_VERSION = 1
DENSITY_CONTRACT = "operator-cli-density-v1"
FAMILY = "operator_cli"
PARENT_STORY = PARENT
SUITE = SUITE
CASE_ID = CASE_ID
COMMANDS = COMMANDS
REQUIREMENTS = REQUIREMENTS
HELPERS = HELPERS
DUAL_COMMAND = "dual-operator-cli-run"
DUAL_CASE_ID = "DUAL-OPERATOR-CLI-01"
DUAL_SUITE = "dual-operator-cli-v1"
PROFILE_CONTRACT = "dual-operator-cli-profile-v1"
REPORT_CONTRACT = "dual-operator-cli-case-run-v1"
LOCAL_EXECUTOR = "bcs_e2e.local_operator_cli_run.local_operator_cli_run"
LOCAL_STATUS = "LOCAL_OPERATOR_CLI_PASS"
CANONICAL_STATUS = "LOCAL_SUITE_PASS"
PREPROD_INITIAL_VERDICT = "BLOCKED_INVENTORIED"
CONTRACT_REVISION = "8559a7a5258a2cb6750befec187e50aa85060cc6"
EXECUTOR_ROW_INDEX = 9
COVERAGE_ROW_NO = 9
SOURCE_FILE = "apps/bcs/scripts/e2e-test/stories.sh"
SOURCE_SHA256 = "3025c6ee245a2a3b514fba18fe913bb8d1ad42dc37183602521f29c496a4f10f"
STORY_LINE_SPAN = (1784, 1799)
STORY_CALL_SPAN = (1786, 1798)
COMPANION_SOURCES = {
    "cli.sh": "70b77d816d0fb089ccee0a8e272b4126316d386144daa2c9ab174eac36ee4193",
    "cli-stories.sh": "576031c026007346206ec1d77666f04852908f9fec5fe29c9c9e255c5977b13d",
    "group.sh": "2bbb82c417261900265ce39f69aebb01633bb0700c4d7c71c1a2c9d7d9131c1d",
    "common.sh": "44f5445031e82cfb06b75fc35721eb4ed4338539f04ec7364b35386a3fe923b0",
}
MIGRATION_CONTRACT = "operator-cli-migration-contract.json"
PARENT_OBLIGATION_COUNT = 13
DENSITY_OBLIGATION_COUNT = 15
PARSED_OBLIGATION_COUNT = 28
RESIDUAL_COUNT_GAP = 0
SITE_PREFIX = "OPERATOR-CLI"
LEAF_PREFIX = "OPCLI"
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"
UPSTREAM_ISSUE: str | None = None
WEIGHTED = False

RUNNER_ROOT = Path(__file__).resolve().parents[2]
E2E_TEST_DIR = RUNNER_ROOT.parent / "e2e-test"
MIGRATION_CONTRACT_PATH = RUNNER_ROOT / MIGRATION_CONTRACT

CLI_SH = "cli.sh"
CLI_STORIES_SH = "cli-stories.sh"
GROUP_SH = "group.sh"
COMMON_SH = "common.sh"
STORIES_SH = "stories.sh"
SOURCE_DISPLAY = {
    STORIES_SH: "apps/bcs/scripts/e2e-test/stories.sh",
    CLI_SH: "apps/bcs/scripts/e2e-test/cli.sh",
    CLI_STORIES_SH: "apps/bcs/scripts/e2e-test/cli-stories.sh",
    GROUP_SH: "apps/bcs/scripts/e2e-test/group.sh",
    COMMON_SH: "apps/bcs/scripts/e2e-test/common.sh",
}

# The parent story calls the thirteen helpers in this exact order
# (stories.sh lines 1786-1798).  Twelve helpers live in cli.sh; the shared
# direct-onboard journey helper lives in cli-stories.sh; the group-create
# helper lives in group.sh outside the cli.sh parse face.
PARENT_CALL_LINES = (1786, 1787, 1788, 1789, 1790, 1791, 1792, 1793, 1794,
                     1795, 1796, 1797, 1798)
HELPERS_FILE_MAP = {
    "test_cli_health": CLI_SH,
    "test_cli_list": CLI_SH,
    "test_cli_get": CLI_SH,
    "test_cli_discover": CLI_SH,
    "test_cli_visibility_get_set": CLI_SH,
    "test_cli_connect": CLI_SH,
    "test_cli_onboard": CLI_SH,
    "_story_cli_direct_onboard": CLI_STORIES_SH,
    "test_cli_update_status": CLI_SH,
    "test_group_create_via_cli": GROUP_SH,
    "test_cli_chat": CLI_SH,
    "test_cli_list_groups": CLI_SH,
    "test_cli_friend": CLI_SH,
}

# The thirteen reviewed adapter methods of the dual face, frozen here and
# re-checked live against remote_operator_cli_adapter at obligation time.
ADAPTER_METHODS = (
    "operator_health",
    "operator_list",
    "operator_get",
    "operator_discover",
    "operator_visibility_round_trip",
    "operator_connect",
    "operator_onboard_web",
    "operator_direct_onboard",
    "operator_update_status",
    "operator_create_group",
    "operator_chat_with_pm",
    "operator_list_groups",
    "operator_friend_list",
)

# The twenty-one once-only product-CLI operations the owned-local executor
# and the dual runner freeze for this story, re-checked live against
# operator_cli_runner at obligation time.
CLI_OPERATIONS = (
    "operator_cli.health",
    "operator_cli.list",
    "operator_cli.get",
    "operator_cli.discover",
    "operator_cli.visibility",
    "operator_cli.connect",
    "operator_cli.onboard_web",
    "operator_cli.list_after_web_onboard",
    "operator_cli.onboard_direct",
    "operator_cli.onboard_readback",
    "operator_cli.update_status",
    "operator_cli.create_group",
    "operator_cli.get_group",
    "operator_cli.friend_request_chat",
    "operator_cli.friend_accept_chat",
    "operator_cli.chat",
    "operator_cli.list_groups",
    "operator_cli.friend_request_list",
    "operator_cli.friend_accept_list",
    "operator_cli.friend_list",
)


@dataclass(frozen=True)
class AtomSpec:
    """One source-anchored density leaf: a frozen (file, span) + field face."""

    leaf_id: str
    parent_requirement: str
    helper: str
    source_name: str
    line_start: int
    line_end: int
    site_id: str
    slot: str
    field: str
    comparator: str
    expected: Any
    expectation: str


# Fifteen source-anchored atoms in story order.  Visibility contributes two
# leaves (set-public and exact restore), direct-onboard two (persist trio and
# lifecycle 404 closure), and chat two (run/session handle and the
# chat-run status+cancel pair); create-group is parent-charged only.
CATALOG = (
    AtomSpec(
        leaf_id="OPCLI.F01", parent_requirement="OPERATOR-CLI.health",
        helper="test_cli_health", source_name=CLI_SH, line_start=28, line_end=30,
        site_id="OPERATOR-CLI.health.payload", slot="payload", field="payload",
        comparator="contains_any", expected=["status", "ok", "healthy"],
        expectation=(
            "cli.sh lines 28-30: the unauthenticated bcs-cli health invocation "
            "exits zero and its stdout payload contains one of the frozen "
            "health markers status, ok or healthy, with the fail guard and the "
            "TESTS_* bookkeeping outside the anchor staying parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F02", parent_requirement="OPERATOR-CLI.list",
        helper="test_cli_list", source_name=CLI_SH, line_start=46, line_end=50,
        site_id="OPERATOR-CLI.list.uuids", slot="uuids", field="uuids",
        comparator="equals", expected=5,
        expectation=(
            "cli.sh lines 46-50: the five-bot hit loop over the trusted-team "
            "fixture UUIDs (CEO, PM, ENG, QA, CS) counts exactly five "
            " memberships in the bcs-cli list stdout; the list command itself, "
            "its exit-zero fail guard and the harness counters stay "
            "parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F03", parent_requirement="OPERATOR-CLI.get",
        helper="test_cli_get", source_name=CLI_SH, line_start=63, line_end=64,
        site_id="OPERATOR-CLI.get.uuid", slot="uuid", field="uuid",
        comparator="predicate", expected=True,
        expectation=(
            "cli.sh lines 63-64: the targeted bcs-cli get readback predicate "
            "holds, echoing the requested fixture Bot UUID; the get command "
            "exit-zero guard and the ensure_cli_token skip guard stay "
            "parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F04", parent_requirement="OPERATOR-CLI.discover",
        helper="test_cli_discover", source_name=CLI_SH, line_start=78, line_end=79,
        site_id="OPERATOR-CLI.discover.match", slot="match", field="match",
        comparator="predicate", expected=True,
        expectation=(
            "cli.sh lines 78-79: the bcs-cli discover --query predicate holds, "
            "the CS fixture Bot UUID appears for the 客服 fixture-name query; "
            "the discover command exit-zero guard stays parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F05", parent_requirement="OPERATOR-CLI.visibility",
        helper="test_cli_visibility_get_set", source_name=CLI_SH,
        line_start=99, line_end=105,
        site_id="OPERATOR-CLI.visibility.set-public", slot="set-public",
        field="set-public", comparator="contains", expected="public",
        expectation=(
            "cli.sh lines 99-105: the visibility set --value public command "
            "exits zero and the follow-up visibility get stdout contains "
            "public, proving the set took effect; the original-value capture "
            "guard above the anchor stays parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F06", parent_requirement="OPERATOR-CLI.visibility",
        helper="test_cli_visibility_get_set", source_name=CLI_SH,
        line_start=116, line_end=122,
        site_id="OPERATOR-CLI.visibility.restore", slot="restore",
        field="restore", comparator="equals", expected="public",
        expectation=(
            "cli.sh lines 116-122 (helper window cli.sh:97-122): the original "
            "value is captured as $before and the exact restore command resets "
            "visibility to it; the anchor check at line 121 is the shell contain "
            'predicate _cli_contains "$BCS_CLI_STDOUT" "$before" and the seeded '
            "trusted-team fixture original value is public, so the frozen "
            "comparison is equals on the string public per the frozen "
            "handoff decision; the capture guard lines 97-115 stay "
            "parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F07", parent_requirement="OPERATOR-CLI.connect",
        helper="test_cli_connect", source_name=CLI_SH, line_start=138, line_end=143,
        site_id="OPERATOR-CLI.connect.session", slot="session", field="session",
        comparator="predicate", expected=True,
        expectation=(
            "cli.sh lines 138-143: the bcs-cli connect predicate holds, the "
            "session/token handle appears in stdout with any token-bearing "
            "bytes scrubbed before projection; the connect exit-zero guard "
            "stays parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F08", parent_requirement="OPERATOR-CLI.onboard-web",
        helper="test_cli_onboard", source_name=CLI_SH, line_start=159, line_end=166,
        site_id="OPERATOR-CLI.onboard-web.url", slot="url", field="url",
        comparator="contains_any", expected=["register", "cli_e2e_bot_probe"],
        expectation=(
            "cli.sh lines 159-166: the non-mutating bcs-cli onboard --web leg "
            "exits zero and the registration URL stdout contains both the "
            "register path marker and the frozen probe name "
            "cli_e2e_bot_probe; the probe name fixture line is part of the "
            "composite anchor and no Bot is registered by this leg")),
    AtomSpec(
        leaf_id="OPCLI.F09", parent_requirement="OPERATOR-CLI.direct-onboard",
        helper="_story_cli_direct_onboard", source_name=CLI_STORIES_SH,
        line_start=89, line_end=94,
        site_id="OPERATOR-CLI.direct-onboard.persist", slot="persist",
        field="persist", comparator="predicate", expected=True,
        expectation=(
            "cli-stories.sh lines 89-94: the direct onboard command persists "
            "the freshly registered temporary agent and the three frozen "
            "assert_json_eq fields hold verbatim, bot_uuid equal to the "
            "registered id, onboarded equal to true and name equal to the "
            "chosen bot name; the upstream register-token and register POST "
            "prerequisite legs above the anchor stay parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F10", parent_requirement="OPERATOR-CLI.direct-onboard",
        helper="_story_cli_direct_onboard", source_name=CLI_STORIES_SH,
        line_start=96, line_end=102,
        site_id="OPERATOR-CLI.direct-onboard.lifecycle", slot="lifecycle",
        field="lifecycle", comparator="predicate", expected=True,
        expectation=(
            "cli-stories.sh lines 96-102: the direct-onboard lifecycle "
            "predicate holds, the onboarded agent reads back once with its "
            "capabilities.name, the exact bot delete exits zero, and the "
            "follow-up read returns the frozen 404 closure; each require_status "
            "leg inside the anchor is a frozen expectation line")),
    AtomSpec(
        leaf_id="OPCLI.F11", parent_requirement="OPERATOR-CLI.update-status",
        helper="test_cli_update_status", source_name=CLI_SH,
        line_start=180, line_end=193,
        site_id="OPERATOR-CLI.update-status.status", slot="status",
        field="status", comparator="predicate", expected=True,
        expectation=(
            "cli.sh lines 180-193: the update-status --status idle broadcast "
            "exits zero, the Status acknowledgement appears in stdout, the "
            "independent registry readback before and after shows the record "
            "unchanged (presence broadcast is echoed, not retained), and the "
            "best-effort busy restore exits zero; the composite anchor covers "
            "the command guards, the acknowledgement predicate and the restore "
            "line")),
    AtomSpec(
        leaf_id="OPCLI.F12", parent_requirement="OPERATOR-CLI.chat",
        helper="test_cli_chat", source_name=CLI_SH, line_start=200, line_end=212,
        site_id="OPERATOR-CLI.chat.handle", slot="handle", field="handle",
        comparator="contains_any",
        expected=["Run:", "Session:", '"run_id"', '"session_id"'],
        expectation=(
            "cli.sh lines 200-212: the detached bcs-cli chat command against "
            "the PM fixture exits zero (the blocking retry inside the anchor "
            "is the frozen fallback shape) and the run/session handle stdout "
            "contains one of the frozen handle markers Run:, Session:, the "
            "lowercase JSON key run_id or session_id; the prerequisite "
            "in-case friendship admissions stay parent-charged inside the "
            "OPERATOR-CLI.chat expectation")),
    AtomSpec(
        leaf_id="OPCLI.F13", parent_requirement="OPERATOR-CLI.chat",
        helper="test_cli_chat", source_name=CLI_SH, line_start=219, line_end=238,
        site_id="OPERATOR-CLI.chat.run-status-cancel", slot="run-status-cancel",
        field="run-status-cancel", comparator="exit_zero", expected=0,
        expectation=(
            "cli.sh lines 219-238: the run_id extraction, the chat-run status "
            "read and the authoritative chat-run cancellation both exit zero "
            "for the admitted run; the composite anchor covers the jq "
            "extraction, the status pass leg and the cancel pass leg")),
    AtomSpec(
        leaf_id="OPCLI.F14", parent_requirement="OPERATOR-CLI.list-groups",
        helper="test_cli_list_groups", source_name=CLI_SH,
        line_start=250, line_end=272,
        site_id="OPERATOR-CLI.list-groups.shape", slot="shape", field="shape",
        comparator="equals", expected="1",
        expectation=(
            "cli.sh lines 250-272: the paginated group-list shape validator "
            "prints the frozen string 1, proving items is a list, returned "
            "equals the item count, total is an integer at least returned, "
            "has_more is boolean and every entry carries group_id or id; the "
            "list-groups command exit-zero guard stays parent-charged")),
    AtomSpec(
        leaf_id="OPCLI.F15", parent_requirement="OPERATOR-CLI.friend-list",
        helper="test_cli_friend", source_name=CLI_SH, line_start=291, line_end=293,
        site_id="OPERATOR-CLI.friend-list.uuid", slot="friend-uuid",
        field="friend-uuid", comparator="predicate", expected=True,
        expectation=(
            "cli.sh lines 291-293: after the single in-case CEO-ENG friendship "
            "admission, the friend list predicate holds and the ENG fixture "
            "Bot UUID appears in the stdout; the friend list command "
            "exit-zero guard and the idempotent admission note stay "
            "parent-charged")),
)

LEAF_IDS = tuple(row.leaf_id for row in CATALOG)
SITE_IDS = tuple(row.site_id for row in CATALOG)

# Stage spans with no machine-readable check site: the group-create helper
# and its get-group readback live in group.sh outside the
# stories.sh/cli.sh/cli-stories.sh upstream parse face, so OPERATOR-CLI.
# create-group stays a parent obligation with field enforcement
# parent-charged; the owned-local executor and dual runner still freeze its
# operator_cli.create_group / operator_cli.get_group steps and the exact
# creator-scoped delete disposition as evidence.
ZERO_ATOM_PARENTS = {
    "OPERATOR-CLI.create-group": (
        "stories.sh line 1795 only calls test_group_create_via_cli, whose "
        "create-group/get-group/exact-delete helpers live in group.sh outside "
        "the stories.sh/cli.sh/cli-stories.sh upstream parse face; field "
        "enforcement stays parent-charged and the owned-local executor steps "
        "operator_cli.create_group plus operator_cli.get_group with the "
        "creator-scoped cleanup disposition are frozen as dual-face evidence"),
}

NOT_CHARGED_PROBES = (
    "the shell harness exits-zero fail guards around each bcs_cli invocation, "
    "the TESTS_PASSED/TESTS_FAILED/TESTS_TOTAL bookkeeping counters, the "
    "ensure_cli_token skip_case guards, the visibility original-value capture "
    "guard above cli.sh line 99, the upstream register-token fetch and "
    "register POST prerequisite legs above the direct-onboard persist anchor, "
    "and the chat prerequisite in-case friendship admissions recorded by the "
    "owned-local executor; the density catalog charges exactly the frozen "
    "assertion spans and every adjacent probe stays parent-charged")

PREPROD_GAPS = (
    "PREPROD_OPERATOR_CLI_FIVE_BOT_HEALTH_LEG_BLOCKED",
    "PREPROD_OPERATOR_CLI_R5_LEAF_BLOCKED_LINEAGE",
    "PREPROD_OPERATOR_CLI_DUAL_CREDENTIALS_UNREVIEWED",
    "PREPROD_OPERATOR_CLI_FULL_STORY_NOT_EXECUTED_OFFLINE",
)


def _source_paths() -> dict[str, Path]:
    # Built per call so tests can patch the module path helpers.
    return {name: E2E_TEST_DIR / name for name in
            (STORIES_SH, CLI_SH, CLI_STORIES_SH, GROUP_SH, COMMON_SH)}


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _function_span(text: str, name: str) -> tuple[int, int, str]:
    """Return (start_line, end_line, body_text) for one shell function."""
    pattern = "(?m)^" + re.escape(name) + r"\(\) \{$"
    found = list(re.finditer(pattern, text))
    if len(found) != 1:
        raise RunStateError("operator-cli shell function is not frozen: " + name)
    start = found[0].start()
    end = text.index("\n}", start) + 2
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, end) + 1
    return start_line, end_line, text[start:end]


def _call_lines(body: str, body_start_line: int,
                helpers: tuple[str, ...]) -> list[int]:
    """Locate the parent story call lines for each helper, in order."""
    body_lines = body.splitlines()
    lines: list[int] = []
    for helper in helpers:
        pattern = "^    " + re.escape(helper) + r"( \|\| return)?$"
        found = [line_number for line_number, line in enumerate(
            body_lines, start=body_start_line)
            if re.match(pattern, line)]
        if len(found) != 1:
            raise RunStateError("operator-cli story call line is not frozen: "
                                + helper)
        lines.append(found[0])
    return lines


def binding_requirements_face() -> dict[str, Any]:
    """Re-run the committed source binding and return its live face."""
    return operator_cli_source_binding()
