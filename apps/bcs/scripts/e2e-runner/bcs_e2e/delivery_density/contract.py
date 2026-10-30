"""Density projection contract for the dual Delivery Policy slice.

Mirrors the structured-density contract contract layout for the
``story_human_manages_delivery_policy`` story: ten reviewed coarse parent
obligations plus 26 field-level density obligations projected from the 13
anchored upstream field sites, a 36-point parsed roster with a per-row source
anchor, the not-charged probe disclosure, and the honest parsed reconciliation
across the committed snapshot caliber (22), the migration-queue score (36),
and this projection (36).

The wrapper command constant is declared here so the mainline serial
registration imports one authoritative name for parser, dispatch and
receipt bookkeeping.
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
    POLICY_PATH,
    SOURCE_FILE,
    build_delivery_density_obligations,
    delivery_density_reconciliation,
)

SCHEMA_VERSION = 1
PROJECTION_CONTRACT = "delivery-density-projection-v1"
REPORT_CONTRACT = "dual-delivery-policy-density-case-run-v1"
CASE_ID = "DUAL-DELIVERY-POLICY-DENSITY-01"
DUAL_DELIVERY_POLICY_DENSITY_COMMAND = "dual-delivery-policy-density-run"

DENSITY_PARENT_COUNT = COARSE_PARENT_COUNT
DENSITY_FIELD_COUNT = FIELD_OBLIGATION_COUNT
DENSITY_ASSERTION_COUNT = PARSED_OBLIGATION_COUNT

# The ten reviewed density operations, derived from the coarse obligation ids
# with the flat dual family's operation naming convention.
DENSITY_OPERATION_ROWS: tuple[dict[str, Any], ...] = tuple(
    {
        "requirement_id": row.obligation_id,
        "operation": row.operation,
        "method": row.method,
        "path": row.path,
        "caller": row.caller,
    }
    for row in build_delivery_density_obligations()
    if row.level == "PARENT"
)
DENSITY_OPERATIONS = frozenset(row["operation"] for row in DENSITY_OPERATION_ROWS)


def delivery_density_plan() -> dict[str, Any]:
    """Return the anchor-validated 36-point density projection plan."""
    rows = build_delivery_density_obligations()
    parents = [row for row in rows if row.level == "PARENT"]
    fields = [row for row in rows if row.level == "FIELD"]
    if len(parents) != DENSITY_PARENT_COUNT or len(fields) != DENSITY_FIELD_COUNT:
        raise ValueError("delivery density projection counts changed")
    parent_ids = {row.obligation_id for row in parents}
    if any(row.parent_obligation not in parent_ids for row in fields):
        raise ValueError("delivery density field row lost its parent projection")
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
            "each anchored upstream field site projects two executable density "
            "obligations (envelope presence/JSON shape and comparator value); "
            "parents stay 1:1 with the ten coarse DELIVERY.* obligations"),
        "obligations": [row.as_dict() for row in rows],
        "not_charged_source_probes": list(NOT_CHARGED_SOURCE_PROBES),
        "reconciliation": delivery_density_reconciliation(),
        "wrapper_command": DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
        "source_file": SOURCE_FILE,
        "source_read_only": True,
    }
