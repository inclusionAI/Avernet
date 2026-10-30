"""Honest local replay and preprod NOT_RUN receipt projection."""
from __future__ import annotations

import json
from typing import Any

from ..run_state import RunStateError
from .contract import Obligation, structured_collaboration_density_plan
from .defect import PREPROD_GAP, assert_honesty_ledger
from .profile import validate_density_profile
from .transport import DensityTransport, Observation, canonical_replay_transport

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED = "BLOCKED_INVENTORIED"


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


def _expected(row: Obligation, context: dict[str, Any]) -> Any:
    if row.expected == "GROUP_ID":
        return context["group_id"]
    if row.expected == "DEFINITION_ID":
        return context["definition_id"]
    if row.expected == "RUN_ID":
        return context["run_id"]
    if row.expected == "MISSING_VERSION":
        return context["missing_version"]
    if row.expected == "BASE_VERSION_PLUS_ONE":
        return context["base_version"] + 1
    return row.expected


def _evaluate(row: Obligation, observation: Observation, context: dict[str, Any]) -> tuple[bool, Any]:
    if row.level == "parent":
        expected = row.expected
        return (observation.status in expected if isinstance(expected, tuple) else observation.status == expected), observation.status
    if row.field == "templates[*].id":
        values = observation.body.get("templates", []) if isinstance(observation.body, dict) else []
        return _expected(row, context) in [item.get("id") for item in values if isinstance(item, dict)], values
    if row.field == "body" and row.expected == []:
        return observation.body == [], observation.body
    observed = _dig(observation.body, row.field)
    expected = _expected(row, context)
    if expected == "NON_EMPTY":
        return _non_empty(observed), observed
    if row.field == "body" or row.field == "error.body":
        haystack = json.dumps(observation.body, ensure_ascii=False, sort_keys=True)
        if expected in ("DEFINITION_ID", "MISSING_VERSION"):
            expected = str(_expected(row, context))
        return str(expected) in haystack, haystack
    return observed == expected, observed


def _bind(row: Obligation, observation: Observation, context: dict[str, Any]) -> None:
    if row.operation == "group.create":
        context["group_id"] = _dig(observation.body, "id")
    elif row.operation == "definition.read":
        context["definition_id"] = _dig(observation.body, "default_definition.id")
        context["base_version"] = _dig(observation.body, "default_definition.version")
        context["missing_version"] = context["base_version"] + 100
    elif row.operation == "definition.patch":
        context["patched_version"] = _dig(observation.body, "default_definition.version")
    elif row.operation == "run.start":
        context["run_id"] = _dig(observation.body, "run.run_id")


def _receipt(row: Obligation, status: str, *, observed: Any = None, error_type: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "obligation_id": row.obligation_id,
        "parent_obligation": row.parent_id,
        "level": row.level,
        "family": row.family,
        "status": status,
        "density": row.density,
        "source": row.as_dict()["source"],
        "operation": row.operation,
    }
    if observed is not None:
        value["observed"] = observed
    if error_type is not None:
        value["error_type"] = error_type
    return value


def run_structured_collaboration_density(*, profile: dict[str, Any], transport: DensityTransport | None = None) -> dict[str, Any]:
    validate_density_profile(profile)
    assert_honesty_ledger()
    plan = structured_collaboration_density_plan()
    rows = tuple(plan["obligations"])
    if transport is None and profile["environment"]["tier"] == "local":
        transport = canonical_replay_transport()
    if transport is None:
        receipts = [_receipt_from_dict(row, STATUS_NOT_RUN, "PREPROD_EXECUTION_NOT_RUN") for row in rows]
        return {
            "contract": plan["contract"], "parent": plan["parent"], "status": STATUS_BLOCKED,
            "environment": {"tier": profile["environment"]["tier"], "entrypoint": profile["entrypoint"]["kind"]},
            "case_scope": plan["accounting"], "assertions": receipts,
            "execution": {"status": STATUS_NOT_RUN, "completed_count": 0, "total_count": len(rows)},
            "gap": dict(PREPROD_GAP), "remote_validation": STATUS_NOT_RUN,
            "grants_release_credit": False,
        }
    context: dict[str, Any] = {"group_id": "", "definition_id": "", "base_version": 0, "missing_version": 0, "run_id": ""}
    receipts: list[dict[str, Any]] = []
    failed = False
    for row in parsed_rows(plan):
        if failed:
            receipts.append(_receipt(row, STATUS_NOT_RUN, error_type="BLOCKED_AFTER_ASSERTION_FAILURE"))
            continue
        try:
            observation = transport.request(row.operation, row.obligation_id)
            _bind(row, observation, context)
            passed, observed = _evaluate(row, observation, context)
            status = STATUS_PASS if passed else STATUS_FAIL
            receipts.append(_receipt(row, status, observed=observed))
            failed = not passed
        except Exception as error:
            receipts.append(_receipt(row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
    status = STATUS_FAIL if failed else STATUS_PASS
    return {
        "contract": plan["contract"], "parent": plan["parent"], "status": status,
        "environment": {"tier": profile["environment"]["tier"], "entrypoint": profile["entrypoint"]["kind"]},
        "case_scope": plan["accounting"], "assertions": receipts,
        "execution": {"status": status, "completed_count": sum(item["status"] == STATUS_PASS for item in receipts), "total_count": len(rows)},
        "remote_validation": STATUS_NOT_RUN, "grants_release_credit": False,
    }


def parsed_rows(plan: dict[str, Any]) -> tuple[Obligation, ...]:
    return tuple(_obligation_from_dict(row) for row in plan["obligations"])


def _obligation_from_dict(row: dict[str, Any]) -> Obligation:
    source = row["source"]
    return Obligation(row["obligation_id"], row["parent_obligation"], row["family"], row["level"], row["method"], source["line_start"], source["line_end"], source["quote"], row["operation"], row["field"], row["expected"], row["density"])


def _receipt_from_dict(row: dict[str, Any], status: str, error_type: str) -> dict[str, Any]:
    return {"obligation_id": row["obligation_id"], "parent_obligation": row["parent_obligation"], "level": row["level"], "family": row["family"], "status": status, "density": row["density"], "source": row["source"], "operation": row["operation"], "error_type": error_type}
