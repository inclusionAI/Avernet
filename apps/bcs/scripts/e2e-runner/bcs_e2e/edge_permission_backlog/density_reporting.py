"""Family-level reporting projection for the Wave-D3 density overlay.

The summary keeps the inventory narrative consistent with the committed
coverage-map rows #32-#35/#39 and the 2026-10-31 assertion-gap snapshot: five
DUAL parents whose preprod legs recorded PASS (r5/r6/r7 rounds) while their
local projections PASS through the frozen families, plus a 22-leaf density
overlay that closes the parsed denominators (frozen 6 + density 22 == parsed
28) without enrolling anything yet.

Counts are computed from the frozen registries offline; the per-leaf
classification counts are attached only when the recorded preprod receipts are
attached, and every gap closes with an explicit material, defect or roster
disclosure instead of silent credit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .density_contract import (
    DENSITY_CASE_KEYS,
    DENSITY_LEAF_COUNT,
    ENVELOPE_STATUS,
    EXISTING_TOTAL,
    PARSED_TOTAL,
    PREPROD_NOT_RUN,
    PROJECTED_SUPERSET_BY_PARENT,
    PARENT_BY_CASE_KEY,
)
from .density_local_arm import density_local_arm_receipt
from .density_obligations import (
    ADDED_BY_PARENT,
    EXISTING_BY_PARENT,
    PARSED_BY_PARENT,
    PARENTS,
    build_density_obligations,
    density_reconciliation,
    source_digest,
)
from .density_preprod_arm import density_preprod_arm_receipt

FAMILY_REPORT_CONTRACT = "dual-edge-permission-density-family-report-v1"
SCHEMA_VERSION = 1

MATERIAL_GAPS = (
    {
        "gap_id": "G-DUAL-LEAFKEYS",
        "kind": "material_gap",
        "statement": (
            "the recorded r5/r6/r7 dual receipts journal one step-zero data "
            "projection per case, not per density leaf; per-leaf projection "
            "keys (visibility writes, pre-decision listings, the mutual pair, "
            "per-direction friend listings, the all-direction decided-row "
            "union, per-entry public discovery shape) are not carried"),
        "owner": "runner mainline",
        "material_id": "M-DENSITY-RECEIPT-LEAFKEYS",
        "blocks": "per-leaf ENROLLMENT classification credit",
    },
    {
        "gap_id": "G-READ-LOCAL-ROUND",
        "kind": "material_gap",
        "statement": (
            "the frozen read slice has no owned-local round retention of its "
            "own in the wpc mutation round (LEG_ABSENT); its local verification "
            "rides the frozen dual-edge-read-run family envelope (r5 read round "
            "and r7 friend-connections state)"),
        "owner": "runner mainline",
        "material_id": "M-DENSITY-RECEIPT-LEAFKEYS",
        "blocks": "read-parent local leg narration only, not the frozen slice verdict",
    },
    {
        "gap_id": "G-ROSTER-PARSED-OPACITY",
        "kind": "roster_gap",
        "statement": (
            "the parsed denominators come from the 2026-10-31 roster snapshot; "
            "the roster does not disclose per-site decomposition, so the "
            "decomposition_rule of the reconciliation module is the honest "
            "basis (existing frozen obligations + anchored density leaves == parsed)"),
        "owner": "coverage accounting",
        "material_id": None,
        "blocks": "none for execution; baseline accounting narrative only",
    },
    {
        "gap_id": "G-MUTUAL-CEO-ENG-HISTORY",
        "kind": "local_round_gap",
        "statement": (
            "the owned-local mutual seam runs the CEO<->QA pair; the historical "
            "CEO<->ENG residual rows are not reconstructable offline, and the "
            "frozen family already documents that correction"),
        "owner": "runner mainline",
        "material_id": None,
        "blocks": "none for the overlay; freeze-family narrative only",
    },
)

PRODUCT_DEFECTS_ON_FILE = (
    {
        "defect_id": "DUP-CREATE-ID-ECHO",
        "statement": (
            "the r5 duplicate branch kept one idempotent pending row but echoed "
            "two distinct request ids; row idempotence holds, the id echo "
            "diverges"),
        "disclosed_by": "EDGE-ERROR-ADMISSION.duplicate-create-idempotent",
    },
    {
        "defect_id": "PD-PRIVATE-CREATE-500",
        "statement": (
            "the canonical contract refuses a private-target create with 403 "
            "(PrivateBotCannotCollaborate); the recorded preprod leg answered "
            "500 with data.error_code internal_error"),
        "disclosed_by": "EDGE-CONNECT-DISCOVER.private-target-create-rejected",
    },
    {
        "defect_id": "PD-DISCOVER-BRANCH-DRIFT",
        "statement": (
            "the organization discovery without a requester bot fails closed "
            "with an error body but answered 403 where the canonical contract "
            "refuses 400, and the role branch wraps the canonical "
            "role_requires_organization_code in invalid_operation"),
        "disclosed_by": ("EDGE-CONNECT-DISCOVER.discover-organization-fail-closed; "
                         "EDGE-CONNECT-DISCOVER.discover-role-requires-organization"),
    },
)

NET_NEW_GRANULARITY_NOTE = (
    "EDGE-ERROR-ADMISSION.actor-kind-human-form is NET_NEW_GRANULARITY: the "
    "frozen umbrella expectation does not enumerate the actor_kind=human "
    "query form, while the owned-local seam literal pins the no_edge refusal; "
    "the freeze family note keeps the correction on file")


def family_summary(evidence_root: Path | None = None) -> dict[str, Any]:
    """Project the five-case density family without triggering any transport."""
    cases: list[dict[str, Any]] = []
    for case_key in DENSITY_CASE_KEYS:
        parent = PARENT_BY_CASE_KEY[case_key]
        local_arm = density_local_arm_receipt(case_key, evidence_root)
        preprod_arm = density_preprod_arm_receipt(case_key, evidence_root)
        cases.append({
            "case_key": case_key,
            "case_id": local_arm["case_id"],
            "public_case_id": local_arm.get("public_case_id"),
            "parent_id": parent,
            "roster": {
                "parsed": PARSED_BY_PARENT[parent],
                "frozen_existing": EXISTING_BY_PARENT[parent],
                "density_added": ADDED_BY_PARENT[parent],
                "projected_superset": PROJECTED_SUPERSET_BY_PARENT[parent]
                if parent in PROJECTED_SUPERSET_BY_PARENT else None,
                "projected_superset_total": PROJECTED_SUPERSET_BY_PARENT[parent],
            },
            "leaves_in_case": ADDED_BY_PARENT[parent],
            "local_status": local_arm["status"],
            "preprod_status": preprod_arm["preprod_status"],
            "family_receipt_availability": preprod_arm["family_receipt_availability"],
            "leaf_classification_totals": preprod_arm["classification_totals"],
            "identity_cross_check": preprod_arm["identity_cross_check"],
            "local_conflicts": local_arm["conflicts"],
            "preprod_conflicts": preprod_arm["conflicts"],
        })
    attached = bool(evidence_root is not None)
    totals = {
        "case_count": len(cases),
        "parsed_total": PARSED_TOTAL,
        "frozen_existing_total": EXISTING_TOTAL,
        "density_leaves_total": DENSITY_LEAF_COUNT,
        "projected_superset_total": EXISTING_TOTAL + DENSITY_LEAF_COUNT,
    }
    if attached:
        totals["classification"] = {
            status: sum(row["leaf_classification_totals"].get(status, 0)
                        for row in cases)
            for status in ("MATCH", "MATCH_PARTIAL", "DRIFT_DISCLOSED",
                           "NOT_OBSERVABLE")}
        totals["classification_total"] = sum(
            row["leaf_classification_totals"].get("total", 0) for row in cases)
    not_observable_ids, partial_ids, match_ids = [], [], []
    if attached:
        for case_key in DENSITY_CASE_KEYS:
            arm = density_preprod_arm_receipt(case_key, evidence_root)
            for row in arm["leaf_classifications"]:
                bucket = {"MATCH": match_ids, "MATCH_PARTIAL": partial_ids,
                          "NOT_OBSERVABLE": not_observable_ids}.get(row["status"])
                if bucket is not None:
                    bucket.append(row["obligation_id"])
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": FAMILY_REPORT_CONTRACT,
        "suite": "dual-edge-permission-density-v1",
        "status": ENVELOPE_STATUS,
        "cases": cases,
        "totals": totals,
        "honest_counts": {
            "frozen_existing_obligations": EXISTING_TOTAL,
            "density_leaves": DENSITY_LEAF_COUNT,
            "parsed_denominator": PARSED_TOTAL,
            "superset_closes_roster": EXISTING_TOTAL + DENSITY_LEAF_COUNT == PARSED_TOTAL,
            "enrolled": 0,
            "grants_release_credit": False,
        },
        "classified_leaf_buckets": {
            "MATCH": match_ids, "MATCH_PARTIAL": partial_ids,
            "NOT_OBSERVABLE": not_observable_ids,
        },
        "material_gaps": list(MATERIAL_GAPS),
        "product_defects_on_file": list(PRODUCT_DEFECTS_ON_FILE),
        "net_new_granularity_note": NET_NEW_GRANULARITY_NOTE,
        "parents": list(PARENTS),
        "reconciliation_roster_snapshot": density_reconciliation()["roster_snapshot"],
        "source_digest": source_digest(),
        "basis": (
            "dual preprod legs recorded PASS in the r5/r6/r7 rounds with "
            "remote_validation NOT_RUN; owned-local journeys are recorded PASS "
            "in the wpc round; the 22 density leaves close the parsed "
            "denominators offline and stay NOT_ENROLLED until an authorized "
            "enrolled round journals per-leaf projection keys"),
        "promotion_guard": (
            "no local model projection, no recorded preprod row and no frozen "
            "family verdict is promoted into density release credit; the "
            "optional command dual-edge-permission-density-run stays "
            "describe-only and unregistered"),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "evidence_hygiene": (
            "profiles captured by digest only; receipts reference environment "
            "variable names, never values; no transport in this projection"),
    }
