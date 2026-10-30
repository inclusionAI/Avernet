"""Offline execution and honest receipt projection for the density slice.

The executor evaluates the frozen 14-clause roster (S13, stories.sh
L782-L804, flow guard counted) against an injected transport.  It never opens
a network connection, never launches a process, and never resolves a
credential: profiles are admitted structurally and captured by digest only.

Three honesty gates are structural:

* no injected transport means every clause stays ``NOT_RUN`` and the receipt
  status stays ``BLOCKED`` — for both tiers — so a planned leg can never be
  laundered into PASS;
* the cancel/terminal cluster (C09-C14) is defect-gated by the on-file
  DM-TERMINAL-CANCEL-PROJECTION: rows evaluate the raw parsed clauses
  truthfully, but the receipt discloses the gate and claims zero DM
  obligation credit;
* ``remote_validation`` stays ``NOT_RUN`` and ``grants_release_credit`` stays
  false on every receipt this module can produce.
"""

from __future__ import annotations

import json
from typing import Any

from ..profile import fingerprint
from ..run_state import RunStateError
from .contract import (
    DENSITY_ASSERTION_COUNT,
    DENSITY_OPERATIONS,
    FAMILY_OBLIGATION_TOTAL,
    PARSED_CLAUSE_COUNT,
    PARSED_MINUS_PARENT_OBLIGATIONS,
    PARENT_OBLIGATION_COUNT,
    ParsedDensityAssertion,
    direct_conversation_density_plan,
    parsed_density_assertions,
)
from .defect import (
    DEFECT_GATED_ASSERTION_IDS,
    DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID,
    defect_gate_descriptor,
    gap_ledger_descriptor,
)
from .profile import validate_density_profile
from .transport import DensityTransport

DENSITY_OPERATION_NAMES = frozenset(DENSITY_OPERATIONS)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED = "BLOCKED"
TRANSPORT_NOT_INJECTED = "DENSITY_TRANSPORT_NOT_INJECTED"
BLOCKED_AFTER_FAILURE = "DENSITY_BLOCKED_AFTER_ASSERTION_FAILURE"
JOURNEY_BLOCKED = "DENSITY_BLOCKED_BY_JOURNEY_ADMISSION"
ASSERTION_FAILED = "DENSITY_ASSERTION_FAILED"
PROJECTION_CONTRACT = "direct-conversation-density-projection-v1"


def _dig(value: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _non_empty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _render(value: Any) -> Any:
    """Render an observed value the way common.sh assert_json_eq digests it."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def _resolve_expected(row: ParsedDensityAssertion, context: dict[str, Any],
                      member_bot_id: str) -> Any:
    expected = row.expected
    if expected == "RUN_ID":
        return context.get("run_id")
    if expected == "MEMBER_BOT_ID":
        return member_bot_id
    return expected


def _evaluate(row: ParsedDensityAssertion, observation: Observation,
              context: dict[str, Any], member_bot_id: str) -> tuple[bool, Any]:
    if row.kind == "status":
        return observation.status == row.expected, observation.status
    observed = _dig(observation.body, row.field)
    if row.expected == "NON_EMPTY":
        return _non_empty(observed), observed
    expected = _resolve_expected(row, context, member_bot_id)
    if row.field == "run_id" and row.expected == "RUN_ID":
        return observed == expected, observed
    return _render(observed) == _render(expected), observed


def _seed_context(observation: Observation, context: dict[str, Any]) -> None:
    if not isinstance(observation.body, dict):
        return
    for field in ("run_id", "session_id", "bot_uuid"):
        value = observation.body.get(field)
        if isinstance(value, str) and value:
            context[field] = value


def _row_receipt(row: ParsedDensityAssertion, status: str, *, observed: Any = None,
                 error_type: str | None = None) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "assertion_id": row.assertion_id,
        "clause_no": row.clause_no,
        "parent_id": row.parent_id,
        "stage": row.stage,
        "kind": row.kind,
        "status": status,
        "source": {
            "file": "apps/bcs/scripts/e2e-test/stories.sh",
            "line": row.source_line,
            "quote": row.source_quote,
            "operation": row.operation,
            "field": row.field,
            "expected": row.expected,
            "located": row.located,
        },
        "density": row.density,
    }
    if observed is not None:
        receipt["observed"] = observed
    if error_type is not None:
        receipt["error_type"] = error_type
    if row.assertion_id in DEFECT_GATED_ASSERTION_IDS:
        receipt["defect_gate"] = {
            "defect_id": DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID,
            "status": "PRODUCT_DEFECT_ON_FILE",
            "raw_clause_evaluation": (
                "evaluates the frozen parsed clause against the raw observation; "
                "PASS here is clause truth, never DM obligation credit"
            ),
        }
    return receipt


def _credential_env_refs(profile: dict[str, Any]) -> list[str]:
    names: list[str] = []
    auth = profile.get("auth")
    if isinstance(auth, dict):
        for value in auth.values():
            if isinstance(value, str) and value and value == value.upper() and " " not in value:
                names.append(value)
    return names


def _receipt(*, profile: dict[str, Any], assertion_receipts: list[dict[str, Any]],
             status: str, error_type: str | None = None,
             blocked_by: str | None = None, transport_injected: bool) -> dict[str, Any]:
    plan = direct_conversation_density_plan()
    pass_count = sum(1 for item in assertion_receipts if item["status"] == STATUS_PASS)
    fail_count = sum(1 for item in assertion_receipts if item["status"] == STATUS_FAIL)
    not_run_count = sum(1 for item in assertion_receipts if item["status"] == STATUS_NOT_RUN)
    return {
        "schema_version": 1,
        "contract": PROJECTION_CONTRACT,
        "case_id": "DUAL-DIRECT-CHAT-01",
        "parent": plan["parent"],
        "status": status,
        "environment": {
            "tier": profile["environment"]["tier"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "executor": (
            "owned-local-offline-rehearsal"
            if profile["environment"]["tier"] == "local"
            else "preprod-direct-chat-gateway-legacy"
        ),
        "profile_digest": fingerprint(profile),
        "local_offline_rehearsal": profile["environment"]["tier"] == "local",
        "case_scope": {
            "parent_obligation_count": PARENT_OBLIGATION_COUNT,
            "parsed_clause_count": PARSED_CLAUSE_COUNT,
            "parsed_minus_parent_obligations": PARSED_MINUS_PARENT_OBLIGATIONS,
            "density_assertion_count": DENSITY_ASSERTION_COUNT,
            "family_obligation_total": FAMILY_OBLIGATION_TOTAL,
            "parsed_clause_coverage": f"{PARSED_CLAUSE_COUNT}/{PARSED_CLAUSE_COUNT}",
            "residual_count_gap": 0,
        },
        "execution": {
            "status": status,
            "transport_injected": transport_injected,
            "completed_count": pass_count,
            "failed_count": fail_count,
            "not_run_count": not_run_count,
            "total_count": DENSITY_ASSERTION_COUNT,
            "error_type": error_type,
        },
        "assertions": assertion_receipts,
        "upstream_defect_gates": {
            DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID: defect_gate_descriptor(),
        },
        "gap_ledger": gap_ledger_descriptor(),
        "honesty": {
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "dm_obligation_credit_claimed": False,
            "gated_rows": list(DEFECT_GATED_ASSERTION_IDS),
            "no_fabricated_idempotency_clause": True,
            "cancel_idempotency_gap": "GAP-DC-01",
            "roster_provenance": plan["roster_provenance"],
        },
        "credential_env_refs": _credential_env_refs(profile),
        "operations_bound": sorted(DENSITY_OPERATION_NAMES),
        "blocked_by": blocked_by,
    }


def run_direct_conversation_density(*, profile: dict[str, Any],
                                    transport: DensityTransport | None = None) -> dict[str, Any]:
    """Project the 14 parsed clauses without opening a network connection.

    With no injected transport the receipt is the honest gap receipt: every
    clause NOT_RUN, case status BLOCKED — for both tiers.  With an injected
    transport the clauses evaluate deterministically against the observations;
    a transport failure or a failed clause is evidence, never a silent PASS.
    """
    profile = validate_density_profile(profile)
    rows = parsed_density_assertions()
    if len(rows) != DENSITY_ASSERTION_COUNT:
        raise RunStateError("direct conversation density roster count drifted")
    member_bot_id = profile["journey"]["member_bot_id"]
    if transport is None:
        assertion_receipts = [
            _row_receipt(row, STATUS_NOT_RUN, error_type=TRANSPORT_NOT_INJECTED) for row in rows
        ]
        receipt = _receipt(
            profile=profile, assertion_receipts=assertion_receipts,
            status=STATUS_BLOCKED, error_type=TRANSPORT_NOT_INJECTED,
            transport_injected=False)
        return receipt
    assertion_receipts: list[dict[str, Any]] = []
    context: dict[str, Any] = {}
    failed = False
    for row in rows:
        if failed:
            assertion_receipts.append(_row_receipt(
                row, STATUS_NOT_RUN, error_type=BLOCKED_AFTER_FAILURE))
            continue
        try:
            observation = transport.request(row.operation, row.assertion_id)
            if row.operation.startswith("direct_chat.main.submit"):
                _seed_context(observation, context)
            passed, observed = _evaluate(row, observation, context, member_bot_id)
            row_receipt = _row_receipt(
                row, STATUS_PASS if passed else STATUS_FAIL, observed=observed)
            if row.assertion_id in DEFECT_GATED_ASSERTION_IDS:
                # Defect-gate disclosure: keep the raw observation (including the
                # on-file downstream_error terminal shape) next to the clause verdict.
                row_receipt["observation"] = observation.body
            assertion_receipts.append(row_receipt)
            if not passed:
                failed = True
        except Exception as error:  # transport failures are evidence, not PASS
            assertion_receipts.append(_row_receipt(
                row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
    status = STATUS_PASS if not failed else STATUS_FAIL
    return _receipt(
        profile=profile, assertion_receipts=assertion_receipts, status=status,
        error_type=None if status == STATUS_PASS else ASSERTION_FAILED,
        transport_injected=True)


def blocked_direct_density_receipt(*, profile: dict[str, Any], blocked_by: str,
                                   error_type: str = JOURNEY_BLOCKED) -> dict[str, Any]:
    """Honest NOT_RUN receipt when the DM journey itself fail-closed first.

    This is the shape the describe-only mainline hunk emits when the strict DM
    lifecycle admission (e.g. the cancel convergence gate) blocks the journey
    before any density clause can observe a wire response: the density slice
    stays NOT_RUN instead of inventing FAIL credit against an unexecuted leg.
    """
    profile = validate_density_profile(profile)
    rows = parsed_density_assertions()
    assertion_receipts = [
        _row_receipt(row, STATUS_NOT_RUN, error_type=JOURNEY_BLOCKED) for row in rows
    ]
    return _receipt(
        profile=profile, assertion_receipts=assertion_receipts, status=STATUS_BLOCKED,
        error_type=error_type, blocked_by=blocked_by, transport_injected=False)
