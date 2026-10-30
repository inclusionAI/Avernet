"""Offline execution and receipt projection for structured density."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contract import (
    DENSITY_ASSERTION_COUNT,
    DENSITY_PARENT_COUNT,
    NEW_ASSERTION_COUNT,
    ParsedAssertion,
    parsed_assertions,
    structured_density_plan,
)
from .profile import validate_density_profile
from .transport import Observation, StructuredDensityTransport


STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED = "BLOCKED"


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


def _expected(row: ParsedAssertion, context: dict[str, Any]) -> Any:
    expected = row.expected
    if expected == "GROUP_ID":
        return context["group_id"]
    if expected == "DEFINITION_ID":
        return context["definition_id"]
    if expected == "RUN_ID":
        return context["run_id"]
    if expected == "MISSING_VERSION":
        return context["missing_version"]
    if expected == "BASE_VERSION_PLUS_ONE":
        return context["base_version"] + 1
    return expected


def _evaluate(row: ParsedAssertion, observation: Observation,
              context: dict[str, Any]) -> tuple[bool, Any]:
    if row.kind == "status":
        expected = row.expected
        passed = observation.status in expected if isinstance(expected, tuple) else observation.status == expected
        return passed, observation.status
    expected = _expected(row, context)
    observed = _dig(observation.body, row.field)
    if expected == "NON_EMPTY":
        return _non_empty(observed), observed
    if row.field == "templates[*].id":
        values = observation.body.get("templates", []) if isinstance(observation.body, dict) else []
        return expected in [item.get("id") for item in values if isinstance(item, dict)], values
    if row.field == "body" and expected == []:
        return observation.body == [], observation.body
    if row.field.endswith("error.body") or row.field == "error.body":
        haystack = json.dumps(observation.body, ensure_ascii=False, sort_keys=True)
        if expected in {"DEFINITION_ID", "MISSING_VERSION"}:
            expected = str(_expected(row, context))
        return str(expected) in haystack, haystack
    if row.field == "body" and isinstance(expected, str):
        haystack = json.dumps(observation.body, ensure_ascii=False, sort_keys=True)
        return expected in haystack, haystack
    return observed == expected, observed


def _initial_context() -> dict[str, Any]:
    return {
        "group_id": "group-structured-density-1",
        "definition_id": "definition-structured-density-1",
        "base_version": 7,
        "missing_version": 107,
        "run_id": "run-structured-density-1",
    }


def _receipt_assertion(row: ParsedAssertion, status: str, *, observed: Any = None,
                       error_type: str | None = None) -> dict[str, Any]:
    result = {
        "id": row.assertion_id,
        "parent_id": row.parent_id,
        "status": status,
        "density": row.density,
        "source": {
            "file": "apps/bcs/scripts/e2e-test/stories.sh",
            "line": row.source_line,
            "quote": row.source_quote,
            "operation": row.operation,
            "field": row.field,
            "expected": row.expected,
            "located": row.located,
        },
    }
    if observed is not None:
        result["observed"] = observed
    if error_type is not None:
        result["error_type"] = error_type
    return result


def _profile_digest(profile: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_structured_density(*, profile: dict[str, Any], transport: StructuredDensityTransport | None = None) -> dict[str, Any]:
    """Run the 43-point projection without opening a network connection.

    Local/owned-local execution requires an injected transport (normally a fake
    transport in offline tests).  A preprod bcs-direct profile intentionally
    fail-closes to BLOCKED/NOT_RUN when no transport is supplied; it never
    turns a planned remote leg into PASS.
    """
    profile = validate_density_profile(profile)
    plan = structured_density_plan()
    rows = parsed_assertions()
    context = _initial_context()
    if transport is None:
        assertions = [_receipt_assertion(row, STATUS_NOT_RUN, error_type="PREPROD_EXECUTION_NOT_RUN") for row in rows]
        return {
            "schema_version": 1,
            "contract": "structured-density-projection-v1",
            "parent": plan["parent"],
            "status": STATUS_BLOCKED,
            "environment": {"tier": profile["environment"]["tier"], "entrypoint_kind": profile["entrypoint"]["kind"]},
            "executor": "owned-local" if profile["environment"]["tier"] == "local" else "preprod-bcs-direct-public-v1",
            "profile_digest": _profile_digest(profile),
            "case_scope": {
                "parent_obligation_count": DENSITY_PARENT_COUNT,
                "expected_assertion_count": DENSITY_ASSERTION_COUNT,
                "new_field_assertion_count": NEW_ASSERTION_COUNT,
                "delta": NEW_ASSERTION_COUNT,
            },
            "assertions": assertions,
            "execution": {"status": STATUS_NOT_RUN, "completed_count": 0, "total_count": DENSITY_ASSERTION_COUNT, "error_type": "PREPROD_EXECUTION_NOT_RUN"},
            "reports": {"expected_assertion_count": DENSITY_ASSERTION_COUNT,
                        "observed_assertion_count": len(assertions),
                        "executed_assertion_count": 0},
            "remote_validation": STATUS_NOT_RUN,
            "grants_release_credit": False,
        }

    assertions = []
    failed = False
    for row in rows:
        if failed:
            assertions.append(_receipt_assertion(row, STATUS_NOT_RUN, error_type="BLOCKED_AFTER_ASSERTION_FAILURE"))
            continue
        try:
            observation = transport.request(row.operation, row.assertion_id)
            binder = getattr(transport, "bind_context", None)
            if binder is not None:
                binder(context, row.operation, observation)
            passed, observed = _evaluate(row, observation, context)
            assertions.append(_receipt_assertion(row, STATUS_PASS if passed else STATUS_FAIL, observed=observed))
            if not passed:
                failed = True
        except Exception as error:  # transport failures are evidence, not PASS
            assertions.append(_receipt_assertion(row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
    status = STATUS_PASS if not failed else STATUS_FAIL
    return {
        "schema_version": 1,
        "contract": "structured-density-projection-v1",
        "parent": plan["parent"],
        "status": status,
        "environment": {"tier": profile["environment"]["tier"], "entrypoint_kind": profile["entrypoint"]["kind"]},
        "executor": "owned-local" if profile["environment"]["tier"] == "local" else "preprod-bcs-direct-public-v1",
        "profile_digest": _profile_digest(profile),
        "case_scope": {
            "parent_obligation_count": DENSITY_PARENT_COUNT,
            "expected_assertion_count": DENSITY_ASSERTION_COUNT,
            "new_field_assertion_count": NEW_ASSERTION_COUNT,
            "delta": NEW_ASSERTION_COUNT,
        },
        "assertions": assertions,
        "execution": {
            "status": status,
            "completed_count": sum(item["status"] == STATUS_PASS for item in assertions),
            "total_count": DENSITY_ASSERTION_COUNT,
            "error_type": None if status == STATUS_PASS else "STRUCTURED_DENSITY_ASSERTION_FAILED",
        },
        "reports": {"expected_assertion_count": DENSITY_ASSERTION_COUNT,
                    "observed_assertion_count": len(assertions),
                    "executed_assertion_count": sum(item["status"] != STATUS_NOT_RUN for item in assertions)},
        "remote_validation": STATUS_NOT_RUN,
        "grants_release_credit": False,
    }
