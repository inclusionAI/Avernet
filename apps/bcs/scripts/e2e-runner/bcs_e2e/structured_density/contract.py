"""Source-anchored 43-point projection for structured collaboration.

The committed structured contract has 17 request-level parent obligations.  The
upstream story also exposes 26 field-level assertion sites.  This module keeps
those two layers separate: parent status assertions remain the original 17,
while the 26 density rows are executable child assertions with a direct source
anchor and parent projection.

The source is read-only.  The three shell probes which are not part of the
43-point parsed roster are recorded explicitly as ``UNLOCATED_SOURCE_PROBES``:
flow guards for a generated Group id and the two conditional 409 branches.
They are not silently counted as new coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..local_structured_contract import REQUIREMENTS
from ..run_state import RunStateError

PARENT = "story_user_runs_structured_collaboration"
DENSITY_PARENT_COUNT = 17
NEW_ASSERTION_COUNT = 26
DENSITY_ASSERTION_COUNT = DENSITY_PARENT_COUNT + NEW_ASSERTION_COUNT
SOURCE_FILE = "apps/bcs/scripts/e2e-test/stories.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "stories.sh"


@dataclass(frozen=True)
class ParsedAssertion:
    assertion_id: str
    parent_id: str
    family: str
    kind: str
    source_line: int
    source_quote: str
    operation: str
    field: str
    expected: Any
    density: bool
    located: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.assertion_id,
            "parent_id": self.parent_id,
            "family": self.family,
            "kind": self.kind,
            "source_file": SOURCE_FILE,
            "source_line": self.source_line,
            "source_quote": self.source_quote,
            "operation": self.operation,
            "field": self.field,
            "expected": self.expected,
            "density": self.density,
            "located": self.located,
        }


# The 17 request-level status assertions are the existing parent obligations.
_PARENT_ROWS = (
    ("P01", "Group", 824, 'require_status "user lists collaboration templates" "200"', "templates.list", 200, "STRUCTURED.templates-list"),
    ("P02", "Group", 828, 'require_status "user opens a collaboration template" "200"', "templates.detail", 200, "STRUCTURED.template-detail"),
    ("P03", "Group", 867, 'require_status "user creates a structured collaboration group" "200"', "group.create", 200, "STRUCTURED.group-create"),
    ("P04", "Group", 874, 'require_status "user reads the structured collaboration group" "200"', "group.read", 200, "STRUCTURED.group-read"),
    ("P05", "Group", 878, 'require_status "user reads the bound collaboration definition" "200"', "definition.read", 200, "STRUCTURED.definition-read"),
    ("P06", "Group", 897, 'require_status "user patches the collaboration definition" "200"', "definition.patch", 200, "STRUCTURED.definition-patch"),
    ("P07", "Group", 913, 'require_status "upgrade to an unpublished definition is rejected" "404"', "definition.upgrade", 404, "STRUCTURED.upgrade-missing"),
    ("P08", "Join", 919, 'require_status "user starts the bound state-machine workflow" "202"', "run.start", 202, "STRUCTURED.run-start"),
    ("P09", "Join", 928, 'require_status "user reads state-machine run status" "200"', "run.read", 200, "STRUCTURED.run-read"),
    ("P10", "Join", 933, 'require_status "user inspects state-machine graph" "200"', "run.graph", 200, "STRUCTURED.graph-read"),
    ("P11", "Join", 938, 'require_status "user inspects the active state-machine node" "200"', "run.node", 200, "STRUCTURED.node-read"),
    ("P12", "Join", 943, 'require_status "user checks whether the workflow needs Human input" "200"', "run.pending-human", 200, "STRUCTURED.pending-human-empty"),
    ("P13", "Message", 955, 'require_status "Human response endpoint rejects a bot task node" "400"', "run.human-response", (400, 409), "STRUCTURED.human-response-denied"),
    ("P14", "Leave", 967, 'require_status "user cancels the state-machine run" "200"', "run.cancel", (200, 409), "STRUCTURED.run-cancel"),
    ("P15", "Leave", 972, 'require_status "user reads cancelled state-machine run" "200"', "run.terminal", 200, "STRUCTURED.run-terminal"),
    ("P16", "Leave", 976, 'require_status "user cannot rerun an aborted state-machine run" "409"', "run.rerun", 409, "STRUCTURED.rerun-aborted-denied"),
    ("P17", "Leave", 981, 'require_status "structured collaboration fixture is cleaned up" "200"', "group.delete", 200, "STRUCTURED.group-delete"),
)

# These are the 26 field/error/state child assertions which close the density
# gap.  Source quotes intentionally stay short and exact so an anchor drift is
# visible in review without copying the upstream story into the runner.
_FIELD_ROWS = (
    ("F01", "Group", 825, 'assert_contains "template catalog includes guided answer"', "templates.list", "templates[*].id", "single-bot-guided-answer"),
    ("F02", "Group", 829, 'assert_json_eq "template detail keeps requested id"', "templates.detail", "id", "single-bot-guided-answer"),
    ("F03", "Group", 830, 'assert_json_eq "template detail keeps requested language"', "templates.detail", "lang", "zh-CN"),
    ("F04", "Group", 831, 'assert_json_not_empty "template detail includes authoring YAML"', "templates.detail", "yaml", "NON_EMPTY"),
    ("F05", "Group", 875, 'assert_json_eq "structured group records state-machine strategy"', "group.read", "group_strategy", "state_machine"),
    ("F06", "Group", 882, 'assert_not_empty "bound definition has an id"', "definition.read", "default_definition.id", "NON_EMPTY"),
    ("F07", "Group", 883, 'assert_not_empty "bound definition has a version"', "definition.read", "default_definition.version", "NON_EMPTY"),
    ("F08", "Group", 884, 'assert_json_eq "bound definition preserves original YAML"', "definition.read", "yaml_source", "original"),
    ("F09", "Group", 900, 'assert_eq "definition patch increments version"', "definition.patch", "default_definition.version", "BASE_VERSION_PLUS_ONE"),
    ("F10", "Group", 901, 'assert_contains "definition patch persists revised instruction"', "definition.patch", "body", "Draft and verify"),
    ("F11", "Group", 914, 'assert_contains "missing upgrade target identifies the definition"', "definition.upgrade", "error.body", "DEFINITION_ID"),
    ("F12", "Group", 915, 'assert_contains "missing upgrade target identifies the version"', "definition.upgrade", "error.body", "MISSING_VERSION"),
    ("F13", "Join", 922, 'assert_not_empty "state-machine start returns run id"', "run.start", "run.run_id", "NON_EMPTY"),
    ("F14", "Join", 923, 'assert_json_eq "state-machine run belongs to structured group"', "run.start", "run.group_id", "GROUP_ID"),
    ("F15", "Join", 924, 'assert_json_not_empty "state-machine start returns node state"', "run.start", "nodes.0.node_id", "NON_EMPTY"),
    ("F16", "Join", 929, 'assert_json_eq "run status read-back keeps run id"', "run.read", "run.run_id", "RUN_ID"),
    ("F17", "Join", 930, 'assert_json_not_empty "run status exposes lifecycle state"', "run.read", "run.status", "NON_EMPTY"),
    ("F18", "Join", 934, 'assert_json_eq "graph identifies the active run"', "run.graph", "run.run_id", "RUN_ID"),
    ("F19", "Join", 935, 'assert_json_eq "graph exposes the answer node"', "run.graph", "nodes.0.node_id", "answer"),
    ("F20", "Join", 939, 'assert_json_eq "node detail identifies answer node"', "run.node", "node.node_id", "answer"),
    ("F21", "Join", 940, 'assert_json_eq "node detail belongs to active run"', "run.node", "node.run_id", "RUN_ID"),
    ("F22", "Join", 944, 'assert_eq "bot-only workflow has no pending Human input"', "run.pending-human", "body", []),
    ("F23", "Message", 956, 'assert_contains "Human response rejection identifies the node kind"', "run.human-response", "error.body", "not a human_input node"),
    ("F24", "Leave", 968, 'assert_json_eq "cancelled state-machine run is aborted"', "run.cancel", "run.status", "aborted"),
    ("F25", "Leave", 973, 'assert_json_eq "cancelled state-machine run remains aborted"', "run.terminal", "run.status", "aborted"),
    ("F26", "Leave", 977, 'assert_contains "rerun rejection explains the Failed-only source rule"', "run.rerun", "error.body", "must be failed before rerun"),
)

# These source probes are deliberately not charged to the 43 parsed points.
# Keeping them visible prevents a future report from claiming they were lost.
UNLOCATED_SOURCE_PROBES = (
    {"source_line": 870, "source_quote": 'assert_not_empty "structured group has an id"', "reason": "flow guard used to bind later requests; not in the parsed 43 roster"},
    {"source_line": 953, "source_quote": 'assert_status "Human response endpoint refuses a converged run without a kind error" "409"', "reason": "conditional alternate to parent P13, not a separate parsed field site"},
    {"source_line": 965, "source_quote": 'assert_status "user cancel reports the run is already converged" "409"', "reason": "conditional alternate to parent P14, not a separate parsed field site"},
)


def _parent_rows() -> tuple[ParsedAssertion, ...]:
    rows = []
    for short_id, family, line, quote, operation, expected, parent_id in _PARENT_ROWS:
        rows.append(ParsedAssertion(
            assertion_id=f"STRUCTURED.{short_id}.status",
            parent_id=parent_id,
            family=family,
            kind="status",
            source_line=line,
            source_quote=quote,
            operation=operation,
            field="http_status",
            expected=expected,
            density=False,
        ))
    return tuple(rows)


def _field_rows() -> tuple[ParsedAssertion, ...]:
    rows = []
    parent_by_operation = {
        operation: parent_id for _, _, _, _, operation, _, parent_id in _PARENT_ROWS
    }
    for short_id, family, line, quote, operation, field, expected in _FIELD_ROWS:
        rows.append(ParsedAssertion(
            assertion_id=f"STRUCTURED.{short_id}.field",
            parent_id=parent_by_operation[operation],
            family=family,
            kind="field",
            source_line=line,
            source_quote=quote,
            operation=operation,
            field=field,
            expected=expected,
            density=True,
        ))
    return tuple(rows)


def parsed_assertions() -> tuple[ParsedAssertion, ...]:
    rows = _parent_rows() + _field_rows()
    if len(rows) != DENSITY_ASSERTION_COUNT:
        raise RunStateError("structured density parsed assertion count changed")
    if sum(row.density for row in rows) != NEW_ASSERTION_COUNT:
        raise RunStateError("structured density field assertion count changed")
    return rows


def _validate_source_anchors(rows: tuple[ParsedAssertion, ...]) -> None:
    try:
        lines = SOURCE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RunStateError(f"structured density source is unavailable: {error}") from None
    for row in rows:
        if row.source_line > len(lines) or row.source_quote not in lines[row.source_line - 1]:
            raise RunStateError(f"structured density source anchor changed: {row.assertion_id}")


def structured_density_plan() -> dict[str, Any]:
    rows = parsed_assertions()
    _validate_source_anchors(rows)
    if tuple(row.parent_id for row in _parent_rows()) != REQUIREMENTS:
        raise RunStateError("structured density parent projection changed")
    return {
        "parent": PARENT,
        "parent_obligation_count": DENSITY_PARENT_COUNT,
        "parsed_assertion_count": DENSITY_ASSERTION_COUNT,
        "new_field_assertion_count": NEW_ASSERTION_COUNT,
        "delta": NEW_ASSERTION_COUNT,
        "assertions": [row.as_dict() for row in rows],
        "unlocated_source_probes": list(UNLOCATED_SOURCE_PROBES),
        "unlocated_count": 0,
        "source_file": SOURCE_FILE,
        "source_read_only": True,
    }
