"""Honest offline replay and receipt projection for Group Context density."""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import Obligation, group_context_density_plan, parsed_obligations
from .defect import PREPROD_GAP, assert_honesty_ledger, honesty_ledger
from .profile import validate_density_profile
from .transport import (
    CONTEXT_NAME, UPDATED_CONTENT, GROUP_CONTENT,
    DensityTransport, Observation, canonical_replay_transport,
)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"
BLOCKED_AFTER_ASSERTION_FAILURE = "BLOCKED_AFTER_ASSERTION_FAILURE"
PREPROD_EXECUTION_NOT_RUN = "PREPROD_EXECUTION_NOT_RUN"


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


def _contains_context(rows: Any, name: str, content: str) -> bool:
    return isinstance(rows, list) and any(
        isinstance(row, dict) and row.get("name") == name and row.get("content") == content
        for row in rows)


def _contains_owner_permission(rows: Any, name: str) -> bool:
    return isinstance(rows, list) and any(
        isinstance(row, dict) and row.get("name") == name and row.get("permission") == "WRO"
        for row in rows)


def _evaluate(row: Obligation, observation: Observation) -> tuple[bool, Any]:
    if row.level == "parent":
        return observation.status == 200, observation.status
    body = observation.body
    if row.field == "http_status":
        return observation.status == row.expected, observation.status
    if row.field == "session_id":
        observed = _dig(body, row.field)
        return isinstance(observed, str) and bool(observed), observed
    if row.field == "status":
        observed = _dig(body, row.field)
        return observed == row.expected, observed
    if row.field == "items":
        name = CONTEXT_NAME
        content = UPDATED_CONTENT
        if row.expected == "INITIAL_CONTEXT":
            content = GROUP_CONTENT
        observed = _dig(body, row.field)
        return _contains_context(observed, name, content), observed
    if row.field == "contexts":
        observed = _dig(body, row.field)
        return _contains_owner_permission(observed, CONTEXT_NAME), observed
    raise RunStateError(f"unsupported Group Context density field: {row.field}")


def _receipt(row: Obligation, status: str, *, observed: Any = None,
             error_type: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "obligation_id": row.obligation_id,
        "parent_obligation": row.parent_obligation,
        "level": row.level,
        "density": row.density,
        "status": status,
        "source": row.as_dict()["source"],
        "operation": row.operation,
        "method": row.method,
    }
    if observed is not None:
        value["observed"] = observed
    if error_type is not None:
        value["error_type"] = error_type
    return value


def _not_run_receipt(row: Obligation, error_type: str) -> dict[str, Any]:
    return _receipt(row, STATUS_NOT_RUN, error_type=error_type)


def _base_receipt(profile: dict[str, Any], status: str,
                  assertions: list[dict[str, Any]], error_type: str | None = None,
                  gap: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = group_context_density_plan()
    counts = {name: sum(row["status"] == name for row in assertions)
              for name in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN)}
    receipt: dict[str, Any] = {
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
        "execution": {
            "status": status,
            "completed_count": counts[STATUS_PASS],
            "failed_count": counts[STATUS_FAIL],
            "not_run_count": counts[STATUS_NOT_RUN],
            "total_count": len(assertions),
            "error_type": error_type,
        },
        "assertions": assertions,
        "honesty": honesty_ledger(),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }
    if gap is not None:
        receipt["gap"] = dict(gap)
    return receipt


def run_group_context_density(*, profile: dict[str, Any], transport: DensityTransport | None = None) -> dict[str, Any]:
    """Evaluate all 18 rows using only an injected or canonical local replay."""
    profile = validate_density_profile(profile)
    assert_honesty_ledger()
    rows = parsed_obligations()
    if transport is None and profile["environment"]["tier"] == "local":
        transport = canonical_replay_transport()
    if transport is None:
        assertions = [_not_run_receipt(row, PREPROD_EXECUTION_NOT_RUN) for row in rows]
        return _base_receipt(
            profile, STATUS_BLOCKED_INVENTORIED, assertions,
            error_type=PREPROD_EXECUTION_NOT_RUN, gap=PREPROD_GAP)
    assertions: list[dict[str, Any]] = []
    failed = False
    for row in rows:
        if failed:
            assertions.append(_not_run_receipt(row, BLOCKED_AFTER_ASSERTION_FAILURE))
            continue
        try:
            observation = transport.request(row.operation, row.obligation_id)
            passed, observed = _evaluate(row, observation)
            assertions.append(_receipt(row, STATUS_PASS if passed else STATUS_FAIL, observed=observed))
            failed = not passed
        except Exception as error:
            assertions.append(_receipt(row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
    return _base_receipt(
        profile, STATUS_PASS if not failed else STATUS_FAIL, assertions,
        error_type=None if not failed else "GROUP_CONTEXT_DENSITY_ASSERTION_FAILED")
