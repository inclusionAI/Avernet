"""Frozen 43-obligation density roster for the structured collaboration story."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from ..local_structured_contract import REQUIREMENTS
from ..run_state import RunStateError

SCHEMA_VERSION = 1
PARENT = "story_user_runs_structured_collaboration"
CONTRACT = "structured-collaboration-density-v2"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/stories.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "stories.sh"
SOURCE_SHA256 = "3025c6ee245a2a3b514fba18fe913bb8d1ad42dc37183602521f29c496a4f10f"
STORY_LINE_START = 820
STORY_LINE_END = 982
PARENT_OBLIGATION_COUNT = 17
FIELD_OBLIGATION_COUNT = 26
PARSED_OBLIGATION_COUNT = 43
RESIDUAL_COUNT_GAP = 0


@dataclass(frozen=True)
class Obligation:
    """One source-anchored parsed clause and its parent projection."""

    obligation_id: str
    parent_id: str
    family: str
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
            "parent_obligation": self.parent_id,
            "family": self.family,
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
    ("P01", "Group", "require_status", 824, 824, 'require_status "user lists collaboration templates" "200" || return', "templates.list", 200, "STRUCTURED.templates-list"),
    ("P02", "Group", "require_status", 828, 828, 'require_status "user opens a collaboration template" "200" || return', "templates.detail", 200, "STRUCTURED.template-detail"),
    ("P03", "Group", "require_status", 867, 867, 'require_status "user creates a structured collaboration group" "200" || return', "group.create", 200, "STRUCTURED.group-create"),
    ("P04", "Group", "require_status", 874, 874, 'require_status "user reads the structured collaboration group" "200" || return', "group.read", 200, "STRUCTURED.group-read"),
    ("P05", "Group", "require_status", 878, 878, 'require_status "user reads the bound collaboration definition" "200" || return', "definition.read", 200, "STRUCTURED.definition-read"),
    ("P06", "Group", "require_status", 897, 897, 'require_status "user patches the collaboration definition" "200" || return', "definition.patch", 200, "STRUCTURED.definition-patch"),
    ("P07", "Group", "require_status", 913, 913, 'require_status "upgrade to an unpublished definition is rejected" "404" || return', "definition.upgrade", 404, "STRUCTURED.upgrade-missing"),
    ("P08", "Join", "require_status", 919, 919, 'require_status "user starts the bound state-machine workflow" "202" || return', "run.start", 202, "STRUCTURED.run-start"),
    ("P09", "Join", "require_status", 928, 928, 'require_status "user reads state-machine run status" "200" || return', "run.read", 200, "STRUCTURED.run-read"),
    ("P10", "Join", "require_status", 933, 933, 'require_status "user inspects state-machine graph" "200" || return', "run.graph", 200, "STRUCTURED.graph-read"),
    ("P11", "Join", "require_status", 938, 938, 'require_status "user inspects the active state-machine node" "200" || return', "run.node", 200, "STRUCTURED.node-read"),
    ("P12", "Join", "require_status", 943, 943, 'require_status "user checks whether the workflow needs Human input" "200" || return', "run.pending-human", 200, "STRUCTURED.pending-human-empty"),
    ("P13", "Message", "require_status", 955, 955, 'require_status "Human response endpoint rejects a bot task node" "400" || return', "run.human-response", (400, 409), "STRUCTURED.human-response-denied"),
    ("P14", "Leave", "require_status", 967, 967, 'require_status "user cancels the state-machine run" "200" || return', "run.cancel", (200, 409), "STRUCTURED.run-cancel"),
    ("P15", "Leave", "require_status", 972, 972, 'require_status "user reads cancelled state-machine run" "200" || return', "run.terminal", 200, "STRUCTURED.run-terminal"),
    ("P16", "Leave", "require_status", 976, 976, 'require_status "user cannot rerun an aborted state-machine run" "409" || return', "run.rerun", 409, "STRUCTURED.rerun-aborted-denied"),
    ("P17", "Leave", "require_status", 981, 981, 'require_status "structured collaboration fixture is cleaned up" "200" || return', "group.delete", 200, "STRUCTURED.group-delete"),
)

_FIELD_ROWS = (
    ("F01", "Group", "assert_contains", 825, 825, 'assert_contains "template catalog includes guided answer"', "templates.list", "templates[*].id", "single-bot-guided-answer", "STRUCTURED.templates-list"),
    ("F02", "Group", "assert_json_eq", 829, 829, 'assert_json_eq "template detail keeps requested id"', "templates.detail", "id", "single-bot-guided-answer", "STRUCTURED.template-detail"),
    ("F03", "Group", "assert_json_eq", 830, 830, 'assert_json_eq "template detail keeps requested language"', "templates.detail", "lang", "zh-CN", "STRUCTURED.template-detail"),
    ("F04", "Group", "assert_json_not_empty", 831, 831, 'assert_json_not_empty "template detail includes authoring YAML"', "templates.detail", "yaml", "NON_EMPTY", "STRUCTURED.template-detail"),
    ("F05", "Group", "assert_json_eq", 875, 875, 'assert_json_eq "structured group records state-machine strategy"', "group.read", "group_strategy", "state_machine", "STRUCTURED.group-read"),
    ("F06", "Group", "assert_not_empty", 882, 882, 'assert_not_empty "bound definition has an id"', "definition.read", "default_definition.id", "NON_EMPTY", "STRUCTURED.definition-read"),
    ("F07", "Group", "assert_not_empty", 883, 883, 'assert_not_empty "bound definition has a version"', "definition.read", "default_definition.version", "NON_EMPTY", "STRUCTURED.definition-read"),
    ("F08", "Group", "assert_json_eq", 884, 884, 'assert_json_eq "bound definition preserves original YAML"', "definition.read", "yaml_source", "original", "STRUCTURED.definition-read"),
    ("F09", "Group", "assert_eq", 900, 900, 'assert_eq "definition patch increments version"', "definition.patch", "default_definition.version", "BASE_VERSION_PLUS_ONE", "STRUCTURED.definition-patch"),
    ("F10", "Group", "assert_contains", 901, 901, 'assert_contains "definition patch persists revised instruction"', "definition.patch", "body", "Draft and verify", "STRUCTURED.definition-patch"),
    ("F11", "Group", "assert_contains", 914, 914, 'assert_contains "missing upgrade target identifies the definition"', "definition.upgrade", "error.body", "DEFINITION_ID", "STRUCTURED.upgrade-missing"),
    ("F12", "Group", "assert_contains", 915, 915, 'assert_contains "missing upgrade target identifies the version"', "definition.upgrade", "error.body", "MISSING_VERSION", "STRUCTURED.upgrade-missing"),
    ("F13", "Join", "assert_not_empty", 922, 922, 'assert_not_empty "state-machine start returns run id"', "run.start", "run.run_id", "NON_EMPTY", "STRUCTURED.run-start"),
    ("F14", "Join", "assert_json_eq", 923, 923, 'assert_json_eq "state-machine run belongs to structured group"', "run.start", "run.group_id", "GROUP_ID", "STRUCTURED.run-start"),
    ("F15", "Join", "assert_json_not_empty", 924, 924, 'assert_json_not_empty "state-machine start returns node state"', "run.start", "nodes.0.node_id", "NON_EMPTY", "STRUCTURED.run-start"),
    ("F16", "Join", "assert_json_eq", 929, 929, 'assert_json_eq "run status read-back keeps run id"', "run.read", "run.run_id", "RUN_ID", "STRUCTURED.run-read"),
    ("F17", "Join", "assert_json_not_empty", 930, 930, 'assert_json_not_empty "run status exposes lifecycle state"', "run.read", "run.status", "NON_EMPTY", "STRUCTURED.run-read"),
    ("F18", "Join", "assert_json_eq", 934, 934, 'assert_json_eq "graph identifies the active run"', "run.graph", "run.run_id", "RUN_ID", "STRUCTURED.graph-read"),
    ("F19", "Join", "assert_json_eq", 935, 935, 'assert_json_eq "graph exposes the answer node"', "run.graph", "nodes.0.node_id", "answer", "STRUCTURED.graph-read"),
    ("F20", "Join", "assert_json_eq", 939, 939, 'assert_json_eq "node detail identifies answer node"', "run.node", "node.node_id", "answer", "STRUCTURED.node-read"),
    ("F21", "Join", "assert_json_eq", 940, 940, 'assert_json_eq "node detail belongs to active run"', "run.node", "node.run_id", "RUN_ID", "STRUCTURED.node-read"),
    ("F22", "Join", "assert_eq", 944, 944, 'assert_eq "bot-only workflow has no pending Human input"', "run.pending-human", "body", [], "STRUCTURED.pending-human-empty"),
    ("F23", "Message", "assert_contains", 956, 957, 'assert_contains "Human response rejection identifies the node kind" ' + chr(92), "run.human-response", "error.body", "not a human_input node", "STRUCTURED.human-response-denied"),
    ("F24", "Leave", "assert_json_eq", 968, 968, 'assert_json_eq "cancelled state-machine run is aborted"', "run.cancel", "run.status", "aborted", "STRUCTURED.run-cancel"),
    ("F25", "Leave", "assert_json_eq", 973, 973, 'assert_json_eq "cancelled state-machine run remains aborted"', "run.terminal", "run.status", "aborted", "STRUCTURED.run-terminal"),
    ("F26", "Leave", "assert_contains", 977, 978, 'assert_contains "rerun rejection explains the Failed-only source rule" ' + chr(92), "run.rerun", "error.body", "must be failed before rerun", "STRUCTURED.rerun-aborted-denied"),
)

PROJECTION_EXCLUSIONS = (
    {"line_start": 870, "line_end": 870, "quote": 'assert_not_empty "structured group has an id" "$group_id"', "reason": "flow-bound identifier probe; not a parsed obligation in coverage-map row #3"},
    {"line_start": 871, "line_end": 871, "quote": '[[ -n "$group_id" ]] || return', "reason": "control-flow guard, not a domain assertion obligation"},
    {"line_start": 885, "line_end": 885, "quote": '[[ -n "$definition_id" && -n "$base_version" ]] || return', "reason": "control-flow guard, not a domain assertion obligation"},
    {"line_start": 888, "line_end": 888, "quote": 'patched_yaml="${definition_yaml/Draft a concise release recommendation./Draft and verify a concise release recommendation.}"', "reason": "fixture transformation assignment, not an assertion"},
    {"line_start": 925, "line_end": 925, "quote": '[[ -n "$run_id" ]] || return', "reason": "control-flow guard, not a domain assertion obligation"},
    {"line_start": 952, "line_end": 953, "quote": 'if [[ "$HTTP_STATUS" == "409" ]]; then', "reason": "conditional alternate branch is disclosed separately and not double-counted"},
    {"line_start": 953, "line_end": 953, "quote": 'assert_status "Human response endpoint refuses a converged run without a kind error" "409"', "reason": "conditional alternate to P13, not a separate parsed field obligation"},
    {"line_start": 964, "line_end": 965, "quote": 'if [[ "$HTTP_STATUS" == "409" ]]; then', "reason": "conditional alternate branch is disclosed separately and not double-counted"},
    {"line_start": 965, "line_end": 965, "quote": 'assert_status "user cancel reports the run is already converged" "409"', "reason": "conditional alternate to P14, not a separate parsed field obligation"},
)

UNLOCATED_SOURCE_PROBES = (
    {"line_start": 870, "line_end": 870, "quote": 'assert_not_empty "structured group has an id"', "reason": "flow guard is parsed outside the 43-obligation roster"},
    {"line_start": 953, "line_end": 953, "quote": 'assert_status "Human response endpoint refuses a converged run without a kind error" "409"', "reason": "conditional alternate is not a new parsed obligation"},
    {"line_start": 965, "line_end": 965, "quote": 'assert_status "user cancel reports the run is already converged" "409"', "reason": "conditional alternate is not a new parsed obligation"},
)


def _rows() -> tuple[Obligation, ...]:
    parents = tuple(Obligation(
        obligation_id=f"STRUCTURED_DENSITY.{short_id}", parent_id=parent,
        family=family, level="parent", method=method,
        line_start=line_start, line_end=line_end, quote=quote,
        operation=operation, field="http_status", expected=expected,
        density=False,
    ) for short_id, family, method, line_start, line_end, quote, operation, expected, parent in _PARENT_ROWS)
    fields = tuple(Obligation(
        obligation_id=f"STRUCTURED_DENSITY.{short_id}", parent_id=parent,
        family=family, level="field", method=method,
        line_start=line_start, line_end=line_end, quote=quote,
        operation=operation, field=field, expected=expected,
        density=True,
    ) for short_id, family, method, line_start, line_end, quote, operation, field, expected, parent in _FIELD_ROWS)
    return parents + fields


def parsed_obligations() -> tuple[Obligation, ...]:
    rows = _rows()
    if len(rows) != PARSED_OBLIGATION_COUNT or len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("structured collaboration density roster count or ids changed")
    if sum(not row.density for row in rows) != PARENT_OBLIGATION_COUNT:
        raise RunStateError("structured collaboration density parent count changed")
    if sum(row.density for row in rows) != FIELD_OBLIGATION_COUNT:
        raise RunStateError("structured collaboration density field count changed")
    return rows


def _validate_source_anchors(rows: tuple[Obligation, ...]) -> None:
    try:
        raw = SOURCE_PATH.read_bytes()
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RunStateError(f"structured collaboration source unavailable: {error}") from None
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RunStateError("structured collaboration source sha256 drifted")
    for row in rows:
        if not (STORY_LINE_START <= row.line_start <= row.line_end <= STORY_LINE_END):
            raise RunStateError(f"source line range outside story: {row.obligation_id}")
        if row.quote not in lines[row.line_start - 1]:
            raise RunStateError(f"source anchor drifted: {row.obligation_id}")


def structured_collaboration_density_plan() -> dict[str, Any]:
    rows = parsed_obligations()
    _validate_source_anchors(rows)
    if tuple(row.parent_id for row in rows[:PARENT_OBLIGATION_COUNT]) != REQUIREMENTS:
        raise RunStateError("structured collaboration parent projection changed")
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "parent": PARENT,
        "source": {"file": SOURCE_FILE, "sha256": SOURCE_SHA256,
                   "line_start": STORY_LINE_START, "line_end": STORY_LINE_END},
        "accounting": {
            "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
            "parent_obligation_count": PARENT_OBLIGATION_COUNT,
            "field_atomic_count": FIELD_OBLIGATION_COUNT,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "parsed_coverage": f"{PARSED_OBLIGATION_COUNT}/{PARSED_OBLIGATION_COUNT}",
        },
        "obligations": [row.as_dict() for row in rows],
        "projection_exclusions": list(PROJECTION_EXCLUSIONS),
        "unlocated_source_probes": list(UNLOCATED_SOURCE_PROBES),
        "unlocated_count": 0,
        "source_read_only": True,
    }
