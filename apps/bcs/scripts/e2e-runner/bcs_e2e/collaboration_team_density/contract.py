"""Source-anchored density contract for the collaboration-team CLI Story.

The existing parent contract carries eight coarse obligations.  This package
adds twenty-two atomic rows, so the density roster is exactly the frozen
30-point parsed caliber (8 parents + 22 atoms).  The source is read-only: the
contract records the current ``cli-stories.sh`` SHA-256 and every row carries a
verbatim source quote and line range.  Any source drift fails closed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..collaboration_team_contract import (
    REQUIREMENT_IDS,
    collaboration_team_source_binding,
)
from ..run_state import RunStateError

SCHEMA_VERSION = 1
PARENT = "story_cli_operator_builds_collaboration_team"
DENSITY_CONTRACT = "collaboration-team-density-v1"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/cli-stories.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "cli-stories.sh"
SOURCE_SHA256 = "576031c026007346206ec1d77666f04852908f9fec5fe29c9c9e255c5977b13d"
STORY_LINE_SPAN = (292, 369)
PARSED_OBLIGATION_COUNT = 30
PARENT_OBLIGATION_COUNT = 8
DENSITY_OBLIGATION_COUNT = 22
RESIDUAL_COUNT_GAP = 0
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"

PARENT_METHODS = (
    "protect_customer_service_visibility",
    "request_reject_and_restore_customer_service_friendship",
    "create_and_read_release_group",
    "add_and_read_release_group_member",
    "compare_group_list_with_formal_api",
    "fuse_release_group",
    "complete_release_group",
    "terminate_release_group",
)

@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    level: str
    method: str
    line_start: int
    line_end: int
    quote: str
    parent_obligation: str
    expectation: str
    operation: str
    field: str
    comparator: str

    @property
    def source_file(self) -> str:
        return SOURCE_FILE

    @property
    def source_line(self) -> int:
        return self.line_start

    @property
    def source_quote(self) -> str:
        return self.quote

    @property
    def located(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.obligation_id,
            "level": self.level,
            "method": self.method,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "quote": self.quote,
            "source_file": SOURCE_FILE,
            "parent_obligation": self.parent_obligation,
            "expectation": self.expectation,
            "operation": self.operation,
            "field": self.field,
            "comparator": self.comparator,
            "located": True,
        }


def _row(*, obligation_id: str, level: str, method: str, line_start: int,
         line_end: int, quote: str, parent: str, expectation: str,
         operation: str, field: str, comparator: str) -> Obligation:
    return Obligation(obligation_id, level, method, line_start, line_end, quote,
                      parent, expectation, operation, field, comparator)


_PARENT_ROWS = (
    _row(obligation_id=REQUIREMENT_IDS[0], level="PARENT",
         method=PARENT_METHODS[0], line_start=295, line_end=295,
         quote='    _cli_story_run "customer-service agent protects incoming friendship requests" CS visibility set --value protected || return',
         parent=REQUIREMENT_IDS[0], expectation="CS protected visibility mutation is admitted.",
         operation="friend.visibility.protected", field="status", comparator="status"),
    _row(obligation_id=REQUIREMENT_IDS[1], level="PARENT",
         method=PARENT_METHODS[1], line_start=299, line_end=299,
         quote='    _cli_story_run "engineering requests a protected friendship" ENG friend request --bot-uuid "$BOT_CS_UUID" || return',
         parent=REQUIREMENT_IDS[1], expectation="The exact ENG to CS friendship decision flow is admitted.",
         operation="friend.request.reject", field="relationship", comparator="workflow"),
    _row(obligation_id=REQUIREMENT_IDS[2], level="PARENT",
         method=PARENT_METHODS[2], line_start=325, line_end=325,
         quote='    _cli_story_create_pm_group "CLI release coordination" || return',
         parent=REQUIREMENT_IDS[2], expectation="The PM-owned release Group can be created and read.",
         operation="group.create.read", field="group_id", comparator="identity"),
    _row(obligation_id=REQUIREMENT_IDS[3], level="PARENT",
         method=PARENT_METHODS[3], line_start=332, line_end=333,
         quote='    _cli_story_run "operator adds verification to the group" PM add-member \\\n        --group "$group_id" --bot-uuid "$BOT_QA_UUID" || return',
         parent=REQUIREMENT_IDS[3], expectation="QA is added to the exact PM-owned Group and read back.",
         operation="group.add_member.read", field="participants", comparator="contains"),
    _row(obligation_id=REQUIREMENT_IDS[4], level="PARENT",
         method=PARENT_METHODS[4], line_start=337, line_end=337,
         quote='    _cli_story_run "operator lists groups containing itself" PM list-groups --batch-size 20 || return',
         parent=REQUIREMENT_IDS[4], expectation="CLI and formal bot-group list views can be compared.",
         operation="group.list.compare", field="count", comparator="cross_source"),
    _row(obligation_id=REQUIREMENT_IDS[5], level="PARENT",
         method=PARENT_METHODS[5], line_start=348, line_end=351,
         quote='    _cli_story_run "operator fuses team perspectives" PM fuse --group "$group_id" \\\n        --question "Is the release ready?" \\\n        --participants "$BOT_PM_UUID,$BOT_ENG_UUID,$BOT_QA_UUID" \\\n        --focus "release risk" || return',
         parent=REQUIREMENT_IDS[5], expectation="The owned Group fuse returns a recommendation.",
         operation="group.fuse", field="recommendation", comparator="contains"),
    _row(obligation_id=REQUIREMENT_IDS[6], level="PARENT",
         method=PARENT_METHODS[6], line_start=354, line_end=355,
         quote='    _cli_story_run "operator marks the coordinated group completed" PM group-status \\\n        --group "$group_id" --status completed --reason "release reviewed" || return',
         parent=REQUIREMENT_IDS[6], expectation="The completed Group remains completed and is cleaned up.",
         operation="group.complete.cleanup", field="status", comparator="lifecycle"),
    _row(obligation_id=REQUIREMENT_IDS[7], level="PARENT",
         method=PARENT_METHODS[7], line_start=364, line_end=364,
         quote='    _cli_story_run "operator terminates a CLI-managed group" PM terminate-group --group "$terminated_group_id" || return',
         parent=REQUIREMENT_IDS[7], expectation="The terminated Group reads completed and is cleaned up.",
         operation="group.terminate.cleanup", field="status", comparator="lifecycle"),
)

_DENSITY_ROWS = (
    _row(obligation_id="CLI-COLLAB-TEAM.D01", level="DENSITY", method="assert_protected_update", line_start=296, line_end=296,
         quote='    assert_json_eq "protected visibility update succeeds" "$BCS_CLI_STDOUT" "success" "true"', parent=REQUIREMENT_IDS[0], expectation="Protected visibility update returns success=true.", operation="friend.visibility.protected", field="success", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D02", level="DENSITY", method="assert_protected_readback", line_start=297, line_end=297,
         quote='    assert_json_eq "protected visibility is returned" "$BCS_CLI_STDOUT" "data.visibility" "protected"', parent=REQUIREMENT_IDS[0], expectation="Protected visibility is returned.", operation="friend.visibility.protected", field="data.visibility", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D03", level="DENSITY", method="assert_friend_request", line_start=300, line_end=300,
         quote='    assert_json_eq "CLI friend request succeeds" "$BCS_CLI_STDOUT" "success" "true"', parent=REQUIREMENT_IDS[1], expectation="ENG friend request returns success=true.", operation="friend.request", field="success", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D04", level="DENSITY", method="identify_pending_request", line_start=308, line_end=308,
         quote='    if item.get("from_bot") == sys.argv[1] and item.get("to_bot") == sys.argv[2] and item.get("status") == "pending":', parent=REQUIREMENT_IDS[1], expectation="CS pending list selects the exact ENG to CS pending row.", operation="friend.pending", field="from_bot,to_bot,status", comparator="exact_tuple"),
    _row(obligation_id="CLI-COLLAB-TEAM.D05", level="DENSITY", method="assert_pending_request_id", line_start=311, line_end=311,
         quote='    assert_not_empty "pending CLI friend request is identifiable" "$request_id"', parent=REQUIREMENT_IDS[1], expectation="The exact pending request ID is non-empty.", operation="friend.pending", field="request_id", comparator="non_empty"),
    _row(obligation_id="CLI-COLLAB-TEAM.D06", level="DENSITY", method="assert_friend_rejection", line_start=315, line_end=315,
         quote='    assert_json_eq "CLI friend rejection succeeds" "$BCS_CLI_STDOUT" "success" "true"', parent=REQUIREMENT_IDS[1], expectation="CS rejection returns success=true.", operation="friend.reject", field="success", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D07", level="DENSITY", method="assert_rejected_absent", line_start=320, line_end=320,
         quote='    assert_eq "rejected agent is absent from CLI friend list" "$rejected_absent" "0"', parent=REQUIREMENT_IDS[1], expectation="ENG final friend list omits CS.", operation="friend.list", field="cs_presence", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D08", level="DENSITY", method="assert_public_restore", line_start=323, line_end=323,
         quote='    assert_json_eq "public visibility restore succeeds" "$BCS_CLI_STDOUT" "data.visibility" "public"', parent=REQUIREMENT_IDS[0], expectation="CS visibility is restored to public.", operation="friend.visibility.public", field="data.visibility", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D09", level="DENSITY", method="assert_group_creation_helper", line_start=325, line_end=325,
         quote='    _cli_story_create_pm_group "CLI release coordination" || return', parent=REQUIREMENT_IDS[2], expectation="The story invokes the reviewed PM Group creation helper for release coordination.", operation="group.create", field="helper", comparator="invoked"),
    _row(obligation_id="CLI-COLLAB-TEAM.D10", level="DENSITY", method="assert_group_id_readback", line_start=329, line_end=329,
         quote='    assert_json_eq "CLI group read keeps its id" "$BCS_CLI_STDOUT" "id" "$group_id"', parent=REQUIREMENT_IDS[2], expectation="Group read preserves the created ID.", operation="group.read", field="id", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D11", level="DENSITY", method="assert_group_driver_readback", line_start=330, line_end=330,
         quote='    assert_json_eq "CLI group read keeps its driver" "$BCS_CLI_STDOUT" "driver_bot" "$BOT_PM_UUID"', parent=REQUIREMENT_IDS[2], expectation="Group read preserves PM as driver_bot.", operation="group.read", field="driver_bot", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D12", level="DENSITY", method="assert_member_readback", line_start=335, line_end=335,
         quote='    assert_contains "CLI group read contains the added specialist" "$BCS_CLI_STDOUT" "$BOT_QA_UUID"', parent=REQUIREMENT_IDS[3], expectation="Expanded Group read contains QA.", operation="group.add_member.read", field="member", comparator="contains"),
    _row(obligation_id="CLI-COLLAB-TEAM.D13", level="DENSITY", method="assert_cli_group_count", line_start=340, line_end=340,
         quote='    assert_not_empty "CLI current-bot group list exposes its result count" "$cli_group_count"', parent=REQUIREMENT_IDS[4], expectation="CLI list exposes a non-empty returned count.", operation="group.list.cli", field="returned", comparator="non_empty"),
    _row(obligation_id="CLI-COLLAB-TEAM.D14", level="DENSITY", method="assert_cli_group_contains", line_start=341, line_end=341,
         quote='    assert_contains "CLI current-bot group list contains the managed group" "$BCS_CLI_STDOUT" "$group_id"', parent=REQUIREMENT_IDS[4], expectation="CLI list contains the exact managed Group.", operation="group.list.cli", field="group_id", comparator="contains"),
    _row(obligation_id="CLI-COLLAB-TEAM.D15", level="DENSITY", method="assert_formal_api_status", line_start=343, line_end=343,
         quote='    require_status "CLI group list can be checked against the formal bot-group API" "200" || return', parent=REQUIREMENT_IDS[4], expectation="Formal bot-group API read returns HTTP 200.", operation="group.list.formal_api", field="http_status", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D16", level="DENSITY", method="assert_formal_api_contains", line_start=344, line_end=344,
         quote='    assert_contains "bot-group read-back contains the CLI-managed group" "$RESPONSE" "$group_id"', parent=REQUIREMENT_IDS[4], expectation="Formal bot-group API read contains the exact managed Group.", operation="group.list.formal_api", field="group_id", comparator="contains"),
    _row(obligation_id="CLI-COLLAB-TEAM.D17", level="DENSITY", method="assert_formal_api_count_equal", line_start=346, line_end=346,
         quote='    assert_eq "CLI current-bot count matches the formal bot-group API" "$cli_group_count" "$api_group_count"', parent=REQUIREMENT_IDS[4], expectation="CLI returned count equals formal API item count.", operation="group.list.formal_api", field="returned,item_count", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D18", level="DENSITY", method="assert_fuse_recommendation", line_start=352, line_end=352,
         quote='    assert_contains "CLI fusion returns a recommendation" "$BCS_CLI_STDOUT" "recommendation"', parent=REQUIREMENT_IDS[5], expectation="Fuse response contains a recommendation.", operation="group.fuse", field="recommendation", comparator="contains"),
    _row(obligation_id="CLI-COLLAB-TEAM.D19", level="DENSITY", method="assert_completed_readback", line_start=357, line_end=357,
         quote='    assert_json_eq "CLI group status persists completed" "$BCS_CLI_STDOUT" "status" "completed"', parent=REQUIREMENT_IDS[6], expectation="Completed Group read-back remains completed.", operation="group.complete.read", field="status", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D20", level="DENSITY", method="assert_completed_cleanup", line_start=360, line_end=360,
         quote='    require_status "completed CLI group is cleaned up" "200" || return', parent=REQUIREMENT_IDS[6], expectation="Completed Group cleanup returns HTTP 200.", operation="group.complete.cleanup", field="http_status", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D21", level="DENSITY", method="assert_terminated_readback", line_start=366, line_end=366,
         quote='    assert_json_eq "terminated CLI group is completed" "$BCS_CLI_STDOUT" "status" "completed"', parent=REQUIREMENT_IDS[7], expectation="Terminated Group read-back is completed.", operation="group.terminate.read", field="status", comparator="equals"),
    _row(obligation_id="CLI-COLLAB-TEAM.D22", level="DENSITY", method="assert_terminated_cleanup", line_start=368, line_end=368,
         quote='    require_status "terminated CLI group is cleaned up" "200" || return', parent=REQUIREMENT_IDS[7], expectation="Terminated Group cleanup returns HTTP 200.", operation="group.terminate.cleanup", field="http_status", comparator="equals"),
)


def _validate_source(rows: tuple[Obligation, ...]) -> None:
    if SOURCE_PATH.is_symlink() or not SOURCE_PATH.is_file():
        raise RunStateError("collaboration-team density source is unavailable")
    raw = SOURCE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RunStateError("collaboration-team density source SHA-256 drifted")
    lines = raw.decode("utf-8").splitlines()
    for row in rows:
        if row.line_start < 1 or row.line_end > len(lines) or row.line_start > row.line_end:
            raise RunStateError(f"collaboration-team density anchor range invalid: {row.obligation_id}")
        quote = "\n".join(lines[row.line_start - 1:row.line_end])
        if quote != row.quote:
            raise RunStateError(f"collaboration-team density anchor drifted: {row.obligation_id}")


def obligations() -> tuple[Obligation, ...]:
    rows = _PARENT_ROWS + _DENSITY_ROWS
    if len(_PARENT_ROWS) != PARENT_OBLIGATION_COUNT:
        raise RunStateError("collaboration-team parent density count changed")
    if len(_DENSITY_ROWS) != DENSITY_OBLIGATION_COUNT:
        raise RunStateError("collaboration-team atomic density count changed")
    if len(rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("collaboration-team parsed density count changed")
    if [row.obligation_id for row in _PARENT_ROWS] != list(REQUIREMENT_IDS):
        raise RunStateError("collaboration-team parent IDs changed")
    ids = [row.obligation_id for row in rows]
    if len(ids) != len(set(ids)):
        raise RunStateError("collaboration-team density IDs are not unique")
    if any(row.parent_obligation not in REQUIREMENT_IDS for row in rows):
        raise RunStateError("collaboration-team density parent projection changed")
    binding = collaboration_team_source_binding()
    if binding["parent"] != PARENT:
        raise RunStateError("collaboration-team source parent changed")
    _validate_source(rows)
    return rows


def density_plan() -> dict[str, Any]:
    rows = obligations()
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "parent": PARENT,
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_SHA256,
        "story_line_span": list(STORY_LINE_SPAN),
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "projection_rule": "8 frozen parent obligations plus 22 source-anchored atomic CLI/read-back checks; the formal bot-group API comparison is executable only when its local projection event is present.",
        "obligations": [row.as_dict() for row in rows],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV],
    }
