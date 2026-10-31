"""Honest offline replay and preprod admission-blocked receipt projection.

The local tier replays the frozen 20 business operations against an injected
transport (or the canonical in-memory replay when none is injected) and
evaluates all 42 shell obligations one by one: the frozen status gate, the
business-envelope precondition (exactly code/message/request_id/data), the
value assertions with their binding tokens, and the transfer renewal window
duo (refreshed expiry never shrinks and stays inside the frozen re-stamp
gap). The first failing assertion cascades every later obligation to
NOT_RUN with BLOCKED_AFTER_ASSERTION_FAILURE, mirroring the shell case that
stops at the first ``|| return``.

The preprod tier never executes: the frozen column is
REMOTE_ADMISSION_GATED_READ_ONLY and the named admission gaps stay open, so
every obligation is reported NOT_RUN with its attributed gap ids. An
injected transport on preprod is ignored by design - injection is not an
admission grant. No receipt on either tier claims remote validation or
release credit.
"""
from __future__ import annotations

import json
from typing import Any

from ..run_state import RunStateError
from .contract import (
    BUSINESS_ENVELOPE_KEYS, BOOTSTRAP_HTTP_CALLS, BUSINESS_HTTP_CALLS,
    HTTP_CALL_COUNT, OWNER_BOT_ID, OWNER_USER_ID, TARGET_USER_ID,
    TRANSFER_WINDOW_MAX_RENEWAL_MS, authority_density_plan, parsed_obligations,
)
from .defect import (
    PREPROD_COLUMN, PREPROD_GAP_IDS, admission_diagnosis_basis,
    assert_honesty_ledger, defect_gap_ledger, gap_attribution,
    preprod_admission_gaps,
)
from .profile import validate_density_profile
from .transport import (
    DensityTransport, Observation, canonical_replay_transport,
)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"
BLOCKED_AFTER_ASSERTION_FAILURE = "BLOCKED_AFTER_ASSERTION_FAILURE"
PREPROD_ADMISSION_BLOCKED = "AUTHORITY_PREPROD_ADMISSION_BLOCKED"
ENVELOPE_PRECONDITION_FAILED = "AUTHORITY_DENSITY_ENVELOPE_PRECONDITION_FAILED"
ASSERTION_FAILED = "AUTHORITY_DENSITY_ASSERTION_FAILED"
LOCAL_LIVE_CLOSURE = "42/42"

_BINDING_TOKENS = {
    "OWNER_BOT_ID": OWNER_BOT_ID,
    "OWNER_USER_ID": OWNER_USER_ID,
    "TARGET_USER_ID": TARGET_USER_ID,
}


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


def _resolve(expected: Any) -> Any:
    if expected in _BINDING_TOKENS:
        return _BINDING_TOKENS[expected]
    return expected


def _envelope_problem(observation: Observation) -> dict[str, Any] | None:
    body = observation.body
    if not isinstance(body, dict):
        return {"body_type": type(body).__name__,
                "required_keys": list(BUSINESS_ENVELOPE_KEYS)}
    observed = set(body)
    missing = sorted(set(BUSINESS_ENVELOPE_KEYS) - observed)
    unexpected = sorted(observed - set(BUSINESS_ENVELOPE_KEYS))
    if missing or unexpected:
        return {"observed_keys": sorted(observed), "missing_keys": missing,
                "unexpected_keys": unexpected}
    return None


def _evaluate(row: Any, observation: Observation,
              context: dict[str, Any]) -> tuple[bool, Any]:
    if row.assertion == "require_status":
        return observation.status == row.status, observation.status
    body = observation.body
    observed = _dig(body, row.field)
    if row.assertion == "assert_json_eq":
        expected = _resolve(row.expected)
        if row.expected is None:
            raise RunStateError("json-eq obligation lost its expected value: "
                                + row.obligation_id)
        passed = observed == expected or str(observed) == str(expected)
        return passed, observed
    if row.assertion == "assert_json_array_contains":
        expected = _resolve(row.expected)
        if not isinstance(observed, list):
            return False, observed
        return any(item == expected or str(item) == str(expected)
                   for item in observed), observed
    if row.assertion == "assert_contains":
        expected = str(_resolve(row.expected))
        haystack = json.dumps(observation.body, ensure_ascii=False, sort_keys=True)
        return expected in haystack, haystack
    if row.assertion == "assert_ok":
        initiated = context.get("initiated_expires_at")
        refreshed = _dig(body, row.field)
        window = TRANSFER_WINDOW_MAX_RENEWAL_MS
        renewed = (type(initiated) is int and type(refreshed) is int
                   and refreshed >= initiated
                   and refreshed - initiated < window)
        return renewed, {
            "initiated_expires_at": initiated if type(initiated) is int else None,
            "refreshed_expires_at": refreshed if type(refreshed) is int else None,
            "delta_ms": (refreshed - initiated
                         if type(refreshed) is int and type(initiated) is int else None),
            "renewed": renewed,
            "max_renewal_ms": window,
        }
    raise RunStateError("unsupported authority density assertion kind: "
                        + row.assertion)


def _bind_context(row: Any, observation: Observation,
                  context: dict[str, Any]) -> None:
    if row.operation == "transfer.initiate":
        context["initiated_expires_at"] = _dig(observation.body, "data.expires_at")
    elif row.operation == "transfer.refresh":
        context["refreshed_expires_at"] = _dig(observation.body, "data.expires_at")


def _receipt(row: Any, status: str, *, observed: Any = None,
             error_type: str | None = None,
             gap_ids: tuple[str, ...] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "obligation_id": row.obligation_id,
        "parent_obligation": row.parent_obligation,
        "family": row.family,
        "level": row.level,
        "density": row.density,
        "assertion": row.assertion,
        "status": status,
        "source": row.as_dict()["source"],
        "call": row.as_dict()["call"],
        "operation": row.operation,
        "method": row.method,
        "field": row.field,
        "expected": row.expected,
    }
    if observed is not None:
        value["observed"] = observed
    if error_type is not None:
        value["error_type"] = error_type
    if gap_ids is not None:
        value["gap_ids"] = list(gap_ids)
    return value


def _local_live_pointer() -> dict[str, Any]:
    return {
        "family_case": "dual-authority-run",
        "owned_local_live_obligation_closure": LOCAL_LIVE_CLOSURE,
        "statement": (
            "the landed dual authority family owns the owned-local LIVE 42/42 "
            "closure; this density receipt is an offline projection and "
            "transfers no release credit"),
    }


def _execution(status: str, assertions: list[dict[str, Any]],
               error_type: str | None) -> dict[str, Any]:
    return {
        "status": status,
        "completed_count": sum(row["status"] == STATUS_PASS for row in assertions),
        "failed_count": sum(row["status"] == STATUS_FAIL for row in assertions),
        "not_run_count": sum(row["status"] == STATUS_NOT_RUN for row in assertions),
        "total_count": len(assertions),
        "error_type": error_type,
    }


def _base_receipt(profile: dict[str, Any], status: str,
                  assertions: list[dict[str, Any]], error_type: str | None,
                  plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract": plan["contract"],
        "case_id": plan["case_id"],
        "parent": plan["parent"],
        "status": status,
        "environment": {
            "tier": profile["environment"]["tier"],
            "entrypoint": profile["entrypoint"]["kind"],
        },
        "case_scope": plan["accounting"],
        "execution": _execution(status, assertions, error_type),
        "assertions": assertions,
        "honesty": defect_gap_ledger(),
        "local_live_pointer": _local_live_pointer(),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def _preprod_receipt(profile: dict[str, Any], plan: dict[str, Any],
                     rows: tuple[Any, ...]) -> dict[str, Any]:
    attribution = gap_attribution()
    gaps = preprod_admission_gaps()
    primary = gaps[0]
    entries = []
    for row in rows:
        entries.append(_receipt(
            row, STATUS_NOT_RUN, error_type=PREPROD_ADMISSION_BLOCKED,
            gap_ids=attribution[row.obligation_id]))
    receipt = _base_receipt(profile, STATUS_BLOCKED_INVENTORIED, entries,
                            PREPROD_ADMISSION_BLOCKED, plan)
    receipt["gap"] = dict(primary)
    receipt["preprod_admission"] = {
        "column": PREPROD_COLUMN,
        "posture": "REMOTE_ADMISSION_GATED_READ_ONLY: credential-free route "
                   "probes plus two read-only authenticated GETs only",
        "origin_declared": profile["entrypoint"]["origin"],
        "api_face": profile["entrypoint"]["api_face"],
        "route_binding_review": admission_diagnosis_basis()["route_review"],
        "credential_refs": admission_diagnosis_basis()["credential_refs"],
        "staff_refs": admission_diagnosis_basis()["staff_refs"],
        "signing_key_env_ref": admission_diagnosis_basis()["signing_key_env_ref"],
        "r8_evidence": admission_diagnosis_basis()["r8_evidence"],
        "trio_context": admission_diagnosis_basis()["trio_context"],
        "coverage_map_row": admission_diagnosis_basis()["coverage_map_row"],
        "coverage_map_queue": admission_diagnosis_basis()["coverage_map_queue"],
        "gap_ids": [gap["id"] for gap in gaps],
        "gaps": {gap["id"]: dict(gap) for gap in gaps},
        "gap_attribution": {obligation_id: list(gap_ids)
                            for obligation_id, gap_ids in attribution.items()},
        "transport_injection": (
            "ignored: the preprod tier is admission-gated read-only; an "
            "injected in-memory transport never grants admission and no "
            "obligation is promoted out of NOT_RUN"),
        "local_live_pointer": _local_live_pointer(),
    }
    return receipt


def run_authority_density(*, profile: dict[str, Any],
                          transport: DensityTransport | None = None) -> dict[str, Any]:
    """Evaluate the 42 shell obligations against an injected or replay transport."""
    profile = validate_density_profile(profile)
    assert_honesty_ledger()
    plan = authority_density_plan()
    rows = parsed_obligations()
    if profile["environment"]["tier"] == "preprod":
        return _preprod_receipt(profile, plan, rows)
    canonical = transport is None
    if transport is None:
        transport = canonical_replay_transport()
    assertions: list[dict[str, Any]] = []
    context: dict[str, Any] = {"initiated_expires_at": None,
                               "refreshed_expires_at": None}
    envelope_ops: set[str] = set()
    envelope_evaluations = 0
    envelope_satisfied = True
    failed = False
    for row in rows:
        if failed:
            assertions.append(_receipt(
                row, STATUS_NOT_RUN, error_type=BLOCKED_AFTER_ASSERTION_FAILURE))
            continue
        try:
            observation = transport.request(row.operation, row.obligation_id)
        except Exception as error:
            assertions.append(_receipt(
                row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
            continue
        problem = _envelope_problem(observation)
        envelope_ops.add(row.operation)
        envelope_evaluations += 1
        if problem is not None:
            envelope_satisfied = False
            assertions.append(_receipt(
                row, STATUS_FAIL, observed=problem,
                error_type=ENVELOPE_PRECONDITION_FAILED))
            failed = True
            continue
        _bind_context(row, observation, context)
        try:
            passed, observed = _evaluate(row, observation, context)
        except Exception as error:
            assertions.append(_receipt(
                row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
            continue
        assertions.append(_receipt(
            row, STATUS_PASS if passed else STATUS_FAIL, observed=observed))
        failed = not passed
    receipt = _base_receipt(
        profile, STATUS_FAIL if failed else STATUS_PASS, assertions,
        None if not failed else ASSERTION_FAILED, plan)
    receipt["replay"] = {
        "transport": "in-memory-replay" if canonical else "injected-replay",
        "operations_replayed": len({operation for operation, _ in transport.calls}),
        "obligation_evaluations": len(assertions),
        "envelope_precondition": {
            "required_keys": list(BUSINESS_ENVELOPE_KEYS),
            "operations_admitted": len(envelope_ops),
            "obligation_evaluations_admitted": envelope_evaluations,
            "satisfied": envelope_satisfied,
        },
    }
    receipt["http_matrix"] = {
        "business_operations": BUSINESS_HTTP_CALLS,
        "bootstrap_operations_excluded": BOOTSTRAP_HTTP_CALLS,
        "family_http_matrix": HTTP_CALL_COUNT,
        "bootstrap_exclusion_reason": plan["replay_scope"]["bootstrap_exclusion_reason"],
    }
    return receipt
