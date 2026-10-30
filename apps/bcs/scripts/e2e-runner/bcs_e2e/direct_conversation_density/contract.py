"""Source-anchored 14-clause density projection for the Direct Chat parent.

The frozen Direct-chat migration contract carries five request-level parent
obligations (DM.ping-submit through DM.main-terminal).  The upstream parsed
roster for ``story_user_has_direct_agent_conversation`` — the wave3 upstream
clause map record S13 (``stories.sh`` lines 776-805, ``n_clauses=14``) —
contains fourteen verification clauses, including the ``[[ -n "$run_id" ]] ||
return`` flow guard on line 789, which the parsed roster authoritatively
counts as one of the 14 clauses.  This module keeps the layers separate: the
five DM obligations stay the parent status layer, while the fourteen density
rows are executable child assertions with an exact source anchor and a parent
projection.

The cancel/terminal cluster (C09-C14) is defect-gated: the station's designed
CANCELLED terminal projection carries ``error_message="downstream_error"``
(queued_runtime.rs:85 semantics; DM-DIAG 2026-10-29; coverage-map row 13).
Rows evaluate the raw parsed clauses truthfully against observations and never
claim DM obligation credit; the strict DM admission in
``direct_chat_cli_case_run`` stays fail-closed and untouched.

The roster below is frozen here (self-contained, committed) with exact quotes
so an upstream anchor drift fails closed instead of silently re-basing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..local_direct_chat_contract import REQUIREMENTS, direct_chat_source_binding
from ..run_state import RunStateError

SCHEMA_VERSION = 1
PARENT = "story_user_has_direct_agent_conversation"
CASE_ID = "DUAL-DIRECT-CHAT-01"
DENSITY_CONTRACT = "dual-direct-conversation-density-v1"
SOURCE_FILE = "apps/bcs/scripts/e2e-test/stories.sh"
SOURCE_PATH = Path(__file__).resolve().parents[2].parent / "e2e-test" / "stories.sh"
STORY_FUNCTION = "story_user_has_direct_agent_conversation"
STORY_SPAN = (776, 805)

# Operation vocabulary aligned with the frozen DM leaves in
# bcs_e2e/direct_chat_cli_case_leaf.py (pinned by the alignment test; the
# constants are duplicated here to keep this projection module decoupled).
MAIN_SUBMIT_OPERATION = "direct_chat.main.submit"
MAIN_READ_OPERATION = "direct_chat.main.read"
MAIN_CANCEL_OPERATION = "direct_chat.main.cancel"
MAIN_TERMINAL_OPERATION = "direct_chat.main.terminal"
DENSITY_OPERATIONS = (
    MAIN_SUBMIT_OPERATION,
    MAIN_READ_OPERATION,
    MAIN_CANCEL_OPERATION,
    MAIN_TERMINAL_OPERATION,
)

# Upstream roster provenance.  The committed roster is the frozen source text
# itself (stories.sh L776-805); the wave3 clause map is the read-only diag
# material that fixed the parsed denominator (S13, n_clauses=14, guard counted).
ROSTER_PROVENANCE = {
    "roster_source": "wave3 upstream clause map record S13",
    "roster_reference": "~/.codex/bcs-e2e/wave3/upstream-clause-maps/all_records.json (read-only diag material, not imported)",
    "case_no": 13,
    "clause_count": 14,
    "line_start": 776,
    "line_end": 805,
    "guard_counted": True,
    "guard_note": (
        "The L789 flow guard '[[ -n \"$run_id\" ]] || return' is one of the 14 "
        "parsed clauses (wave3 S13).  It is implemented as an executable guard "
        "row (run_id binding non-empty) rather than silently dropped, unlike the "
        "structured_density family which recorded its guards as UNLOCATED probes."
    ),
}

PING_UPSTREAM_SITE_NOTE = (
    "DM.ping-submit upstream site is the test_bot_chat_async fixture 202 check "
    "in actor.sh (L245-266), not a stories.sh S13 clause; no density row is "
    "fabricated for it and it keeps its parent obligation credit only."
)

DEFECT_GATE_NOTE = (
    "C09-C14 evaluate the raw parsed clauses truthfully; the strict DM "
    "lifecycle admission (DM-TERMINAL-CANCEL-PROJECTION, defect on file) stays "
    "fail-closed in direct_chat_cli_case_run and no DM obligation credit is "
    "claimed by this projection."
)

# Parsed clause count, frozen parent obligations and the accounting deltas.
PARSED_CLAUSE_COUNT = 14
PARENT_OBLIGATION_COUNT = 5
DENSITY_ASSERTION_COUNT = 14
FAMILY_OBLIGATION_TOTAL = PARENT_OBLIGATION_COUNT + DENSITY_ASSERTION_COUNT
PARSED_MINUS_PARENT_OBLIGATIONS = PARSED_CLAUSE_COUNT - PARENT_OBLIGATION_COUNT


@dataclass(frozen=True)
class ParsedDensityAssertion:
    """One parsed upstream clause with a direct stories.sh anchor."""

    assertion_id: str
    clause_no: int
    parent_id: str
    stage: str
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
            "assertion_id": self.assertion_id,
            "clause_no": self.clause_no,
            "parent_id": self.parent_id,
            "stage": self.stage,
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


# (clause, stories.sh line, verbatim upstream clause, stage, kind, operation,
#  field, expected token, projected DM parent)
_ROSTER = (
    ("C01", 782, 'require_status "agent starts an asynchronous direct conversation" "202" || return',
     "async-submit", "status", MAIN_SUBMIT_OPERATION, "http_status", 202, "DM.main-submit"),
    ("C02", 786, 'assert_not_empty "async conversation returns a run id" "$run_id"',
     "async-submit", "field", MAIN_SUBMIT_OPERATION, "run_id", "NON_EMPTY", "DM.main-submit"),
    ("C03", 787, 'assert_not_empty "async conversation returns a session id" "$session_id"',
     "async-submit", "field", MAIN_SUBMIT_OPERATION, "session_id", "NON_EMPTY", "DM.main-submit"),
    ("C04", 788, 'assert_json_eq "async conversation targets product manager" "$RESPONSE" "bot_uuid" "$BOT_PM_UUID"',
     "async-submit", "field", MAIN_SUBMIT_OPERATION, "bot_uuid", "MEMBER_BOT_ID", "DM.main-submit"),
    ("C05", 789, '[[ -n "$run_id" ]] || return',
     "async-submit", "guard", MAIN_SUBMIT_OPERATION, "run_id", "NON_EMPTY", "DM.main-submit"),
    ("C06", 792, 'require_status "caller reads its direct chat run" "200" || return',
     "run-read", "status", MAIN_READ_OPERATION, "http_status", 200, "DM.main-read"),
    ("C07", 793, 'assert_json_eq "chat run read-back keeps run id" "$RESPONSE" "run_id" "$run_id"',
     "run-read", "field", MAIN_READ_OPERATION, "run_id", "RUN_ID", "DM.main-read"),
    ("C08", 794, 'assert_json_not_empty "chat run exposes current state" "$RESPONSE" "state"',
     "run-read", "field", MAIN_READ_OPERATION, "state", "NON_EMPTY", "DM.main-read"),
    ("C09", 797, 'require_status "caller cancels the direct chat run" "200" || return',
     "run-cancel", "status", MAIN_CANCEL_OPERATION, "http_status", 200, "DM.main-cancel"),
    ("C10", 798, 'assert_json_eq "chat run cancellation is acknowledged" "$RESPONSE" "cancelled" "true"',
     "run-cancel", "field", MAIN_CANCEL_OPERATION, "cancelled", "true", "DM.main-cancel"),
    ("C11", 799, 'assert_json_eq "cancelled chat run has cancelled state" "$RESPONSE" "state" "cancelled"',
     "run-cancel", "field", MAIN_CANCEL_OPERATION, "state", "cancelled", "DM.main-cancel"),
    ("C12", 802, 'require_status "caller reads the cancelled chat run" "200" || return',
     "run-terminal-read", "status", MAIN_TERMINAL_OPERATION, "http_status", 200, "DM.main-terminal"),
    ("C13", 803, 'assert_json_eq "cancelled run stays cancelled" "$RESPONSE" "state" "cancelled"',
     "run-terminal-read", "field", MAIN_TERMINAL_OPERATION, "state", "cancelled", "DM.main-terminal"),
    ("C14", 804, 'assert_json_eq "cancelled run is terminal" "$RESPONSE" "is_terminal" "true"',
     "run-terminal-read", "field", MAIN_TERMINAL_OPERATION, "is_terminal", "true", "DM.main-terminal"),
)


def _rows() -> tuple[ParsedDensityAssertion, ...]:
    rows = []
    for clause_no, (clause, line, quote, stage, kind, operation, field, expected, parent) in enumerate(_ROSTER, start=1):
        rows.append(ParsedDensityAssertion(
            assertion_id=f"DIRECT_DENSITY.{clause}",
            clause_no=clause_no,
            parent_id=parent,
            stage=stage,
            kind=kind,
            source_line=line,
            source_quote=quote,
            operation=operation,
            field=field,
            expected=expected,
            density=True,
            located=True,
        ))
    return tuple(rows)


def parsed_density_assertions() -> tuple[ParsedDensityAssertion, ...]:
    rows = _rows()
    if len(rows) != DENSITY_ASSERTION_COUNT:
        raise RunStateError("direct conversation density parsed clause count changed")
    ids = [row.assertion_id for row in rows]
    if len(set(ids)) != DENSITY_ASSERTION_COUNT:
        raise RunStateError("direct conversation density assertion ids are not unique")
    allowed_parents = {
        "DM.main-submit", "DM.main-read", "DM.main-cancel", "DM.main-terminal",
    }
    if allowed_parents != {row.parent_id for row in rows} or not allowed_parents <= set(REQUIREMENTS):
        raise RunStateError("direct conversation density parent projection changed")
    for row in rows:
        if not (STORY_SPAN[0] <= row.source_line <= STORY_SPAN[1]):
            raise RunStateError(f"direct conversation density anchor outside the story span: {row.assertion_id}")
    return rows


def _validate_source_anchors(rows: tuple[ParsedDensityAssertion, ...]) -> None:
    try:
        lines = SOURCE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RunStateError(f"direct conversation density source is unavailable: {error}") from None
    for row in rows:
        if row.source_line > len(lines) or row.source_quote not in lines[row.source_line - 1]:
            raise RunStateError(f"direct conversation density source anchor changed: {row.assertion_id}")


def direct_conversation_density_plan() -> dict[str, Any]:
    """Frozen 14-clause plan with source binding, anchors and accounting."""
    binding = direct_chat_source_binding()
    if [row["id"] for row in binding["requirements"]] != list(REQUIREMENTS):
        raise RunStateError("direct conversation density DM parent projection changed")
    rows = parsed_density_assertions()
    _validate_source_anchors(rows)
    clause_to_parent = {row.assertion_id: row.parent_id for row in rows}
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "parent": PARENT,
        "case_id": CASE_ID,
        "roster_provenance": dict(ROSTER_PROVENANCE),
        "pairing": {
            "upstream_source": SOURCE_FILE,
            "story_function": STORY_FUNCTION,
            "story_line_span": list(STORY_SPAN),
            "parsed_clause_count": PARSED_CLAUSE_COUNT,
            "guard_counted_as_clause": True,
            "ping_upstream_site_note": PING_UPSTREAM_SITE_NOTE,
            "defect_gate_note": DEFECT_GATE_NOTE,
        },
        "accounting": {
            "parent_obligation_count": PARENT_OBLIGATION_COUNT,
            "parsed_clause_count": PARSED_CLAUSE_COUNT,
            "parsed_minus_parent_obligations": PARSED_MINUS_PARENT_OBLIGATIONS,
            "density_assertion_count": DENSITY_ASSERTION_COUNT,
            "family_obligation_total": FAMILY_OBLIGATION_TOTAL,
            "parsed_clause_coverage": f"{PARSED_CLAUSE_COUNT}/{PARSED_CLAUSE_COUNT}",
            "residual_count_gap": 0,
            "delta_basis": (
                "parsed 14 - parent obligations 5 = 9; the 14 density rows close "
                "the clause layer 1:1 (five submit-stage rows including the guard, "
                "three read rows, three cancel rows, three terminal-read rows)"
            ),
        },
        "requirement_clause_map": {
            requirement: sorted(
                clause for clause, parent in clause_to_parent.items() if parent == requirement
            )
            for requirement in REQUIREMENTS if requirement != "DM.ping-submit"
        },
        "assertions": [row.as_dict() for row in rows],
        "unlocated_source_probes": [],
        "unlocated_count": 0,
        "source_file": SOURCE_FILE,
        "source_read_only": True,
    }
