"""Density projection contract for the dual Retired service policy slice.

Mirrors the reviewed dual-density contract layout for the
``story_retired_service_policy`` parent: eight coarse parent obligations
frozen 1:1 with the committed ``SERVICE-POLICY.*`` migration contract plus
thirty-two field-level density obligations projected from the thirty-two
anchored upstream field sites, the forty-point parsed roster with a per-row
source anchor, the not-charged probe disclosure, and the honest parsed
reconciliation across the upstream marker caliber (23), the committed bearer
leg caliber (20), and this projection (40).

The reviewed station-wire face of the journey covers nineteen HTTP
operations: the four bootstrap stations the owned-local story exercises
against the mock admission plane (ensure-human, one-time register token,
register, onboard) plus the fifteen business stations the field obligations
anchor.  The teardown disposition row is a projected derived operation, never
a station binding, and is disclosed instead of bound.  The wrapper command
constant is declared here so the mainline serial registration imports one
authoritative name for parser, dispatch and receipt bookkeeping.
"""

from __future__ import annotations

from typing import Any

from .obligations import (
    COARSE_PARENT_COUNT,
    FIELD_OBLIGATION_COUNT,
    FIELD_SITE_COUNT,
    NOT_CHARGED_SOURCE_PROBES,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    SOURCE_FILE,
    build_service_policy_density_obligations,
    service_policy_density_reconciliation,
)

SCHEMA_VERSION = 1
PROJECTION_CONTRACT = "service-policy-density-projection-v1"
REPORT_CONTRACT = "dual-service-policy-density-case-run-v1"
CASE_ID = "DUAL-SERVICE-POLICY-DENSITY-01"
DUAL_SERVICE_POLICY_DENSITY_COMMAND = "dual-service-policy-density-run"

DENSITY_PARENT_COUNT = COARSE_PARENT_COUNT
DENSITY_FIELD_COUNT = FIELD_OBLIGATION_COUNT
DENSITY_ASSERTION_COUNT = PARSED_OBLIGATION_COUNT

# The four bootstrap stations of the owned-local journey (upstream
# service_policy_story.py lines 57-67).  ensure-human and register are parent
# obligations; the one-time token extraction and the onboarding call are
# marker-charged request-status checks whose station faces are bound here so
# the preprod station form is disclosed completely.  Nothing here resolves a
# credential: preprod registration maps to
# REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER and never registers.
BOOTSTRAP_OPERATION_ROWS: tuple[dict[str, Any], ...] = (
    {"requirement_id": "SERVICE-POLICY.mock-human-bootstrap",
     "operation": "service_policy.ensure_human", "method": "POST",
     "path": "/me/ensure-human", "caller": "human"},
    {"requirement_id": "SERVICE-POLICY.mock-human-bootstrap",
     "operation": "service_policy.register_token", "method": "GET",
     "path": "/register/token", "caller": "human"},
    {"requirement_id": "SERVICE-POLICY.provider-free-bot-registration",
     "operation": "service_policy.register_bot", "method": "POST",
     "path": "/register", "caller": "human"},
    {"requirement_id": "SERVICE-POLICY.provider-free-bot-registration",
     "operation": "service_policy.onboard_bot", "method": "POST",
     "path": "/bots/onboard", "caller": "driver"},
)

# Teardown is a projected derived obligation: the owned-local leg disposes
# three disposable Sessions, two disposable Groups and the registered
# disposable driver Bot (six dispositions); the preprod leg fences the shared
# driver Bot deletion (SKIPPED_SHARED_DRIVER_BOT).  It is never a station
# binding on either tier.
TEARDOWN_DERIVED_OPERATION = "service_policy.teardown_dispositions"


def _business_operation_rows() -> tuple[dict[str, Any], ...]:
    """Derive the fifteen business stations from the anchored roster."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    bootstrap = {row["operation"] for row in BOOTSTRAP_OPERATION_ROWS}
    for row in build_service_policy_density_obligations():
        if row.operation in bootstrap or row.operation == TEARDOWN_DERIVED_OPERATION:
            continue
        if row.operation in seen:
            continue
        seen.add(row.operation)
        rows.append({
            "requirement_id": row.parent_obligation,
            "operation": row.operation,
            "method": row.method,
            "path": row.path,
            "caller": row.caller,
        })
    return tuple(rows)


BUSINESS_OPERATION_ROWS = _business_operation_rows()

# Nineteen reviewed station operations in journey order: four bootstrap
# stations plus the fifteen anchored business stations.
DENSITY_OPERATION_ROWS: tuple[dict[str, Any], ...] = (
    BOOTSTRAP_OPERATION_ROWS + BUSINESS_OPERATION_ROWS)

DENSITY_OPERATIONS = frozenset(row["operation"] for row in DENSITY_OPERATION_ROWS)


def service_policy_density_plan() -> dict[str, Any]:
    """Return the anchor-validated forty-point density projection plan."""
    rows = build_service_policy_density_obligations()
    parents = [row for row in rows if row.level == "PARENT"]
    fields = [row for row in rows if row.level == "FIELD"]
    if len(parents) != DENSITY_PARENT_COUNT or len(fields) != DENSITY_FIELD_COUNT:
        raise ValueError("service policy density projection counts changed")
    parent_ids = {row.obligation_id for row in parents}
    if any(row.parent_obligation not in parent_ids for row in fields):
        raise ValueError("service policy density field row lost its parent projection")
    if len(BUSINESS_OPERATION_ROWS) != 15 or len(DENSITY_OPERATION_ROWS) != 19:
        raise ValueError("service policy density station derivation changed")
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": PROJECTION_CONTRACT,
        "parent": PARENT,
        "parent_obligation_count": DENSITY_PARENT_COUNT,
        "field_site_count": FIELD_SITE_COUNT,
        "field_obligation_count": DENSITY_FIELD_COUNT,
        "parsed_obligation_count": DENSITY_ASSERTION_COUNT,
        "delta": DENSITY_FIELD_COUNT,
        "projection_rule": (
            "parents stay 1:1 with the eight frozen SERVICE-POLICY.* coarse "
            "obligations; the four upstream policy-absence assertions split "
            "into two field obligations per encoding iteration per call site; "
            "compound upstream Python asserts split per field (session kind, "
            "lifecycle status, stored input, distinct session ids, error "
            "shapes, final status/output); each of the thirty-two anchored "
            "field sites projects exactly one executable density obligation"),
        "bootstrap_operations": [dict(row) for row in BOOTSTRAP_OPERATION_ROWS],
        "business_operation_count": len(BUSINESS_OPERATION_ROWS),
        "station_operation_count": len(DENSITY_OPERATION_ROWS),
        "disclosed_non_binding_operations": [
            {"operation": TEARDOWN_DERIVED_OPERATION, "method": "PROJECTED",
             "path": "owned-local-teardown-projection",
             "note": ("derived teardown projection; the owned-local leg "
                      "disposes six resources while the preprod leg fences "
                      "the shared driver Bot deletion")},
        ],
        "obligations": [row.as_dict() for row in rows],
        "not_charged_source_probes": list(NOT_CHARGED_SOURCE_PROBES),
        "reconciliation": service_policy_density_reconciliation(),
        "wrapper_command": DUAL_SERVICE_POLICY_DENSITY_COMMAND,
        "source_file": SOURCE_FILE,
        "source_read_only": True,
    }
