"""Credential-free offline replay for the Draft delivery density contract."""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import (DENSITY_OBLIGATION_COUNT, PARSED_OBLIGATION_COUNT,
                       PARENT_OBLIGATION_COUNT, SOURCE_FILE, density_plan)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"
PRODUCT_DEFECT_302_ID = "DRAFT-DELIVERY-302-IDENTITY-REDIRECT"


def mock_observations() -> dict[str, Any]:
    """Return deterministic local observations; no CLI, socket, or credential is used."""
    return {
        "parents": {
            "DRAFT-DELIVERY.offline-preview": STATUS_PASS,
            "DRAFT-DELIVERY.create-first-revision": STATUS_PASS,
            "DRAFT-DELIVERY.owner-metadata": STATUS_PASS,
            "DRAFT-DELIVERY.shared-read-validation": STATUS_PASS,
            "DRAFT-DELIVERY.fixed-revision-cli": STATUS_PASS,
            "DRAFT-DELIVERY.cas-update-history": STATUS_PASS,
            "DRAFT-DELIVERY.revoke-delete-lifecycle": STATUS_PASS,
            "DRAFT-DELIVERY.journey-completes": STATUS_PASS,
        },
        "fields": {
            f"DRAFT-DENSITY.D{index:02d}": STATUS_PASS for index in range(1, 25)
        },
        "values": {
            "schema_version": 1, "bytes_equal": True, "size_bytes": 2174,
            "validation_valid": True, "revision": 1, "read_token_present": False,
            "head_revision": 1, "shared_read_status": 200,
            "content_hash": "761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7",
            "cache_control": "private, no-store", "shared_validate_status": 200,
            "shared_validate_revision": 1, "public_delete_status": 401,
            "download_bytes_equal": True, "validate_exit_code": 0,
            "update_revision": 2, "historical_size_bytes": 2174,
            "historical_revision": 1, "stale_status": 409,
            "revoke_repeat_exit_code": 0, "revoked_status": 404,
            "old_revision_status": 200, "deleted_status": 404, "late_put_status": 404,
        },
    }


def _value_key(obligation_id: str) -> str:
    return {
        "D01": "schema_version", "D02": "bytes_equal", "D03": "size_bytes",
        "D04": "validation_valid", "D05": "revision", "D06": "read_token_present",
        "D07": "head_revision", "D08": "shared_read_status", "D09": "content_hash",
        "D10": "cache_control", "D11": "shared_validate_status",
        "D12": "shared_validate_revision", "D13": "public_delete_status",
        "D14": "download_bytes_equal", "D15": "validate_exit_code",
        "D16": "update_revision", "D17": "historical_size_bytes",
        "D18": "historical_revision", "D19": "stale_status",
        "D20": "revoke_repeat_exit_code", "D21": "revoked_status",
        "D22": "old_revision_status", "D23": "deleted_status", "D24": "late_put_status",
    }[obligation_id.rsplit(".", 1)[1]]


def _field_status(row: dict[str, Any], observations: dict[str, Any]) -> str:
    field_id = row["id"]
    field_states = observations.get("fields", {})
    if field_id not in field_states:
        return STATUS_NOT_RUN
    status = field_states[field_id]
    if status != STATUS_PASS:
        return STATUS_FAIL
    values = observations.get("values", {})
    if _value_key(field_id) not in values:
        return STATUS_NOT_RUN
    value = values[_value_key(field_id)]
    expected = row["expected"]
    if row["comparator"] == "contains":
        passed = isinstance(value, str) and isinstance(expected, str) and expected.lower() in value.lower()
    else:
        passed = value == expected
    return STATUS_PASS if passed else STATUS_FAIL


def project(observations: dict[str, Any] | None = None, *, tier: str = "local") -> dict[str, Any]:
    """Project observations into a 32-row honest receipt.

    Local replay can pass all rows.  A preprod projection without an injected
    observation is explicitly NOT_RUN and carries the named 302 product defect;
    it never converts a redirect or absent transport into a PASS.
    """
    plan = density_plan()
    if observations is None:
        observations = {} if tier == "preprod" else mock_observations()
    if not isinstance(observations, dict):
        raise RunStateError("collaboration-draft replay observations must be an object")
    rows = []
    for row in plan["obligations"]:
        if row["level"] == "PARENT":
            parent_states = observations.get("parents", {})
            status = parent_states.get(row["id"], STATUS_NOT_RUN)
            if status != STATUS_PASS:
                status = STATUS_FAIL if status not in (STATUS_NOT_RUN, None) else STATUS_NOT_RUN
            event = "parents"
        else:
            status = _field_status(row, observations)
            event = "fields"
        rows.append({
            "obligation_id": row["id"], "level": row["level"], "method": row["method"],
            "status": status,
            "source": {"file": SOURCE_FILE, "line_start": row["line_start"],
                       "line_end": row["line_end"], "quote": row["quote"], "located": True},
            "evidence_event": event,
        })
    counts = {status: sum(item["status"] == status for item in rows)
              for status in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN)}
    overall = STATUS_PASS if counts[STATUS_PASS] == PARSED_OBLIGATION_COUNT else STATUS_FAIL if counts[STATUS_FAIL] else STATUS_BLOCKED_INVENTORIED
    return {
        "contract": plan["contract"], "parent": plan["parent"], "status": overall,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": 0, "counts": counts, "obligations": rows,
        "execution": "owned-local-offline-replay" if tier == "local" else "preprod-not-run",
        "environment_tier": tier, "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "upstream_defects": {
            PRODUCT_DEFECT_302_ID: {
                "status": "PRODUCT_DEFECT_ON_FILE",
                "scope": "preprod bearer-only Draft identity path",
                "observation": "station ACE can return HTTP 302 SSO redirect instead of the legacy Draft response",
                "disposition": "fail-close; no silent cookie or route repair in this density package",
                "blocks": "preprod PASS credit",
            }
        },
    }
