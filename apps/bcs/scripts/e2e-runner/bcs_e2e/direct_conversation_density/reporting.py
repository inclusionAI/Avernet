"""Mechanical summarization of density receipts and proposed accounting notes.

Everything here derives from a receipt produced by
``run_direct_conversation_density``; nothing re-evaluates the station, no
network is touched, and the module only *proposes* text for the committed
coverage map and executor rows — the shared faces themselves stay untouched
this wave (red lines).
"""

from __future__ import annotations

from typing import Any

from .contract import (
    DENSITY_ASSERTION_COUNT,
    FAMILY_OBLIGATION_TOTAL,
    PARSED_CLAUSE_COUNT,
    PARSED_MINUS_PARENT_OBLIGATIONS,
    PARENT_OBLIGATION_COUNT,
)
from .defect import DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID


def density_accounting(receipt: dict[str, Any]) -> dict[str, Any]:
    """Derive the clause/status accounting from a projection receipt."""
    rows = receipt.get("assertions", [])
    counts = {"PASS": 0, "FAIL": 0, "NOT_RUN": 0}
    gated = {"PASS": 0, "FAIL": 0, "NOT_RUN": 0}
    for row in rows:
        status = row.get("status")
        if status in counts:
            counts[status] += 1
        if "defect_gate" in row and status in gated:
            gated[status] += 1
    return {
        "total": len(rows),
        "expected_total": DENSITY_ASSERTION_COUNT,
        "counts": counts,
        "defect_gated_counts": gated,
        "case_status": receipt.get("status"),
        "execution_status": receipt.get("execution", {}).get("status"),
        "transport_injected": receipt.get("execution", {}).get("transport_injected"),
        "remote_validation": receipt.get("honesty", {}).get("remote_validation"),
        "grants_release_credit": receipt.get("honesty", {}).get("grants_release_credit"),
        "open_defect_gates": [DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID],
        "open_gaps": sorted(receipt.get("gap_ledger", {}).keys()),
    }


def family_accounting() -> dict[str, Any]:
    """The frozen family accounting basis (parent layer + density layer)."""
    return {
        "parent_obligation_ids": [
            "DM.ping-submit", "DM.main-submit", "DM.main-read",
            "DM.main-cancel", "DM.main-terminal",
        ],
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_assertion_count": DENSITY_ASSERTION_COUNT,
        "family_obligation_total": FAMILY_OBLIGATION_TOTAL,
        "parsed_clause_count": PARSED_CLAUSE_COUNT,
        "parsed_clause_coverage": f"{PARSED_CLAUSE_COUNT}/{PARSED_CLAUSE_COUNT}",
        "parsed_minus_parent_obligations": PARSED_MINUS_PARENT_OBLIGATIONS,
        "residual_count_gap": 0,
        "execution_gap": "preprod NOT_RUN (GAP-DC-03)",
        "defect_gate": DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID,
    }


def md_table(receipt: dict[str, Any]) -> str:
    """Render the per-clause projection table for retention notes."""
    lines = [
        "| clause | line | parent | kind | status | observed | gate |",
        "|---|---:|---|---|---|---|---|",
    ]
    for row in receipt.get("assertions", []):
        source = row.get("source", {})
        observed = row.get("observed", "")
        if isinstance(observed, (dict, list)):
            observed = "<json>"
        gate = "DM-TERMINAL-CANCEL-PROJECTION" if "defect_gate" in row else ""
        lines.append(
            f"| {row.get('assertion_id', '')} | {source.get('line', '')} | "
            f"{row.get('parent_id', '')} | {row.get('kind', '')} | "
            f"{row.get('status', '')} | {observed} | {gate} |")
    return "\n".join(lines)


def proposed_coverage_map_note(receipt: dict[str, Any]) -> str:
    """Proposed (not applied) coverage-map row 13 note increment."""
    accounting = density_accounting(receipt)
    counts = accounting["counts"]
    return (
        "密度投影 Wave-D1：14/14 parsed clauses 锚定 stories.sh L782-L804"
        "（wave3 S13 roster，含 L789 guard 计为第 5 条）；义务口径 5→19"
        "（DM.* 5 + DIRECT_DENSITY.C01-C14 14，提案）；"
        f"owned-local 离线缺陷忠实回放 {counts['PASS']}/14 PASS"
        "（缺陷门 DM-TERMINAL-CANCEL-PROJECTION 在案披露，GAP-DC-02；"
        "DM 严格 cancel/terminal 准入未动），"
        f"FAIL={counts['FAIL']} NOT_RUN={counts['NOT_RUN']}；"
        "preprod NOT_RUN gap 收据（GAP-DC-03）；remote_validation=NOT_RUN；"
        "grants_release_credit=false"
    )


def proposed_executor_migration_notes(receipt: dict[str, Any]) -> list[str]:
    """Proposed (not applied) executor-migration row 13 notes increment."""
    return [
        (
            "density-projection(direct_conversation_density): parsed 14/14 anchored "
            "(stories.sh L782-L804 incl. L789 guard; wave3 clause map S13); "
            "owned-local offline defect-faithful rehearsal disclosed under "
            "DM-TERMINAL-CANCEL-PROJECTION gate; preprod honest NOT_RUN receipt; "
            "DM strict admission untouched; remote_validation=NOT_RUN; "
            "grants_release_credit=false"
        ),
        (
            "density obligations 5->19 proposed for the committed accounting "
            "anchor; GAP-DC-01 cancel idempotency has no upstream parsed clause "
            "and stays out of the roster (no fabrication)"
        ),
    ]
