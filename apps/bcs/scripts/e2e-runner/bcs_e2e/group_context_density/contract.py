"""Frozen 18-point source projection for the Group Context CLI story."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from ..run_state import RunStateError

SCHEMA_VERSION = 1
PARENT = "story_cli_operator_manages_group_context"
CASE_ID = "DUAL-GROUP-CONTEXT-CLI-DENSITY-01"
CONTRACT = "group-context-density-v1"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/cli-stories.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "cli-stories.sh"
SOURCE_REVISION = "8559a7a5258a2cb6750befec187e50aa85060cc6"
SOURCE_SHA256 = "576031c026007346206ec1d77666f04852908f9fec5fe29c9c9e255c5977b13d"
STORY_LINE_START = 567
STORY_LINE_END = 671
PARENT_OBLIGATION_COUNT = 9
FIELD_OBLIGATION_COUNT = 9
PARSED_OBLIGATION_COUNT = 18
RESIDUAL_COUNT_GAP = 0


@dataclass(frozen=True)
class Obligation:
    """One parent or field-level obligation with an exact source anchor."""

    obligation_id: str
    parent_obligation: str
    level: str
    method: str
    line_start: int
    line_end: int
    quote: str
    operation: str
    field: str
    expected: Any
    density: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "parent_obligation": self.parent_obligation,
            "level": self.level,
            "method": self.method,
            "source": {
                "file": SOURCE_FILE,
                "line_start": self.line_start,
                "line_end": self.line_end,
                "quote": self.quote,
            },
            "operation": self.operation,
            "field": self.field,
            "expected": self.expected,
            "density": self.density,
            "located": True,
        }


_PARENT_ROWS = (
    ("P01", "CLI-GROUP-CONTEXT.workspace", "_cli_story_create_pm_group", 571, 571,
     '    _cli_story_create_pm_group "CLI group-context workspace" || return',
     "group_context.workspace", "operation", "created"),
    ("P02", "CLI-GROUP-CONTEXT.add-group-entry", "_cli_story_run", 586, 596,
     '    _cli_story_run "operator adds a group context entry" PM group-context add \\',
     "group_context.add.group", "operation", "acknowledged"),
    ("P03", "CLI-GROUP-CONTEXT.add-session-entry", "_cli_story_run", 598, 609,
     '    _cli_story_run "operator adds a session context entry" PM group-context add \\',
     "group_context.add.session", "operation", "acknowledged"),
    ("P04", "CLI-GROUP-CONTEXT.add-round-entry", "_cli_story_run", 611, 623,
     '    _cli_story_run "operator adds a round context entry" PM group-context add \\',
     "group_context.add.round", "operation", "acknowledged"),
    ("P05", "CLI-GROUP-CONTEXT.get-initial-entry", "_cli_story_run", 625, 634,
     '    _cli_story_run "operator gets the group context entry" PM group-context get \\',
     "group_context.get.initial", "operation", "read"),
    ("P06", "CLI-GROUP-CONTEXT.update-entry", "_cli_story_run", 636, 646,
     '    _cli_story_run "operator updates the group context entry" PM group-context update \\',
     "group_context.update.group", "operation", "acknowledged"),
    ("P07", "CLI-GROUP-CONTEXT.list-entries", "_cli_story_run", 648, 657,
     '    _cli_story_run "operator lists group context entries" PM group-context list \\',
     "group_context.list.entries", "operation", "read"),
    ("P08", "CLI-GROUP-CONTEXT.get-updated-entry", "_cli_story_run", 659, 667,
     '    _cli_story_run "operator gets the updated group context entry" PM group-context get \\',
     "group_context.get.updated", "operation", "read"),
    ("P09", "CLI-GROUP-CONTEXT.cleanup-workspace", "api_delete", 669, 670,
     '    api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"',
     "group_context.cleanup", "operation", 200),
)

_FIELD_ROWS = (
    ("F01", "CLI-GROUP-CONTEXT.workspace", "assert_not_empty", 574, 574,
     '    assert_not_empty "CLI group-context workspace returns a session id" "$session_id"',
     "group_context.workspace", "session_id", "NON_EMPTY"),
    ("F02", "CLI-GROUP-CONTEXT.add-group-entry", "assert_json_eq", 596, 596,
     '    assert_json_eq "CLI group-context add succeeds" "$BCS_CLI_STDOUT" "status" "ok"',
     "group_context.add.group", "status", "ok"),
    ("F03", "CLI-GROUP-CONTEXT.add-session-entry", "assert_json_eq", 609, 609,
     '    assert_json_eq "CLI group-context session add succeeds" "$BCS_CLI_STDOUT" "status" "ok"',
     "group_context.add.session", "status", "ok"),
    ("F04", "CLI-GROUP-CONTEXT.add-round-entry", "assert_json_eq", 623, 623,
     '    assert_json_eq "CLI group-context round add succeeds" "$BCS_CLI_STDOUT" "status" "ok"',
     "group_context.add.round", "status", "ok"),
    ("F05", "CLI-GROUP-CONTEXT.get-initial-entry", "assert_eq", 634, 634,
     '    assert_eq "CLI group-context get returns the added content" "$get_has_content" "1"',
     "group_context.get.initial", "items", "INITIAL_CONTEXT"),
    ("F06", "CLI-GROUP-CONTEXT.update-entry", "assert_json_eq", 646, 646,
     '    assert_json_eq "CLI group-context update succeeds" "$BCS_CLI_STDOUT" "status" "ok"',
     "group_context.update.group", "status", "ok"),
    ("F07", "CLI-GROUP-CONTEXT.list-entries", "assert_eq", 657, 657,
     '    assert_eq "CLI group-context list returns the updated metadata" "$list_has_item" "1"',
     "group_context.list.entries", "contexts", "OWNER_PERMISSION"),
    ("F08", "CLI-GROUP-CONTEXT.get-updated-entry", "assert_eq", 667, 667,
     '    assert_eq "CLI group-context get returns the updated content" "$get_has_updated" "1"',
     "group_context.get.updated", "items", "UPDATED_CONTEXT"),
    ("F09", "CLI-GROUP-CONTEXT.cleanup-workspace", "require_status", 670, 670,
     '    require_status "CLI group-context group is cleaned up" "200" || return',
     "group_context.cleanup", "http_status", 200),
)

PROJECTION_EXCLUSIONS = (
    {"line_start": 568, "line_end": 568, "quote": '    info "Story: an operator manages Group Context through bcs-cli"',
     "reason": "story narration, not an assertion"},
    {"line_start": 569, "line_end": 584, "quote": '    ctx_name="cli_e2e_context_$$"',
     "reason": "fixture variables and identifier construction, not assertions"},
    {"line_start": 575, "line_end": 578, "quote": '    if [[ -z "$session_id" ]]; then',
     "reason": "control-flow guard and cleanup fallback, not an additional semantic assertion"},
    {"line_start": 586, "line_end": 595, "quote": '    _cli_story_run "operator adds a group context entry" PM group-context add \\',
     "reason": "one command envelope; its response assertion is charged at F02"},
    {"line_start": 625, "line_end": 632, "quote": '    _cli_story_run "operator gets the group context entry" PM group-context get \\',
     "reason": "one command envelope; its response assertion is charged at F05"},
    {"line_start": 636, "line_end": 645, "quote": '    _cli_story_run "operator updates the group context entry" PM group-context update \\',
     "reason": "one command envelope; its response assertion is charged at F06"},
    {"line_start": 648, "line_end": 655, "quote": '    _cli_story_run "operator lists group context entries" PM group-context list \\',
     "reason": "one command envelope; its response assertion is charged at F07"},
    {"line_start": 659, "line_end": 665, "quote": '    _cli_story_run "operator gets the updated group context entry" PM group-context get \\',
     "reason": "one command envelope; its response assertion is charged at F08"},
)


def _rows() -> tuple[Obligation, ...]:
    parents = tuple(Obligation(
        obligation_id=f"GROUP_CONTEXT_DENSITY.{short_id}",
        parent_obligation=parent,
        level="parent",
        method=method,
        line_start=line_start,
        line_end=line_end,
        quote=quote,
        operation=operation,
        field=field,
        expected=expected,
        density=False,
    ) for short_id, parent, method, line_start, line_end, quote, operation, field, expected in _PARENT_ROWS)
    fields = tuple(Obligation(
        obligation_id=f"GROUP_CONTEXT_DENSITY.{short_id}",
        parent_obligation=parent,
        level="field",
        method=method,
        line_start=line_start,
        line_end=line_end,
        quote=quote,
        operation=operation,
        field=field,
        expected=expected,
        density=True,
    ) for short_id, parent, method, line_start, line_end, quote, operation, field, expected in _FIELD_ROWS)
    return parents + fields


def parsed_obligations() -> tuple[Obligation, ...]:
    rows = _rows()
    if len(rows) != PARSED_OBLIGATION_COUNT or len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("Group Context density roster count or IDs changed")
    if sum(row.level == "parent" for row in rows) != PARENT_OBLIGATION_COUNT:
        raise RunStateError("Group Context parent roster changed")
    if sum(row.level == "field" for row in rows) != FIELD_OBLIGATION_COUNT:
        raise RunStateError("Group Context field roster changed")
    return rows


def _validate_source_anchors(rows: tuple[Obligation, ...]) -> None:
    try:
        raw = SOURCE_PATH.read_bytes()
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RunStateError(f"Group Context source unavailable: {error}") from None
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RunStateError("Group Context cli-stories.sh sha256 drifted")
    for row in rows:
        if not (STORY_LINE_START <= row.line_start <= row.line_end <= STORY_LINE_END):
            raise RunStateError(f"Group Context source range changed: {row.obligation_id}")
        if row.quote not in lines[row.line_start - 1]:
            raise RunStateError(f"Group Context source anchor changed: {row.obligation_id}")


def group_context_density_plan() -> dict[str, Any]:
    rows = parsed_obligations()
    _validate_source_anchors(rows)
    parent_ids = tuple(row.parent_obligation for row in rows[:PARENT_OBLIGATION_COUNT])
    if parent_ids != tuple(row[1] for row in _PARENT_ROWS):
        raise RunStateError("Group Context parent projection changed")
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "parent": PARENT,
        "case_id": CASE_ID,
        "source": {
            "file": SOURCE_FILE,
            "sha256": SOURCE_SHA256,
            "source_revision": SOURCE_REVISION,
            "line_start": STORY_LINE_START,
            "line_end": STORY_LINE_END,
        },
        "accounting": {
            "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
            "parent_obligation_count": PARENT_OBLIGATION_COUNT,
            "field_atomic_count": FIELD_OBLIGATION_COUNT,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "parsed_coverage": f"{PARSED_OBLIGATION_COUNT}/{PARSED_OBLIGATION_COUNT}",
        },
        "obligations": [row.as_dict() for row in rows],
        "projection_exclusions": list(PROJECTION_EXCLUSIONS),
        "unlocated_source_probes": [],
        "unlocated_count": 0,
        "source_read_only": True,
    }
