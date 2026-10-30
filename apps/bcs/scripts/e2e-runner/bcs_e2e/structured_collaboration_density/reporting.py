"""Mechanical density accounting and describe-only integration notes."""
from __future__ import annotations

from typing import Any

from .contract import structured_collaboration_density_plan

INTEGRATION_TOKEN = "STRUCTURED_COLLAB_DENSITY_26_OBLIGATIONS_20261031"

def density_accounting(receipt: dict[str, Any]) -> dict[str, Any]:
    counts = {status: sum(row["status"] == status for row in receipt["assertions"]) for status in ("PASS", "FAIL", "NOT_RUN")}
    expected = receipt["case_scope"]["parsed_obligation_count"]
    return {"counts": counts, "expected": expected, "observed": len(receipt["assertions"]), "residual_count_gap": expected - len(receipt["assertions"])}

def family_accounting() -> dict[str, int]:
    plan = structured_collaboration_density_plan()
    return {"parsed": plan["accounting"]["parsed_obligation_count"], "parents": plan["accounting"]["parent_obligation_count"], "field_atomic": plan["accounting"]["field_atomic_count"], "residual_count_gap": plan["accounting"]["residual_count_gap"]}

def md_table(receipt: dict[str, Any]) -> str:
    lines = ["| obligation | status | source |", "|---|---|---|"]
    for row in receipt["assertions"]:
        source = row["source"]
        lines.append(f"| {row['obligation_id']} | {row['status']} | {source['file']}:{source['line_start']}-{source['line_end']} |")
    return "\n".join(lines)

def proposed_executor_migration_notes() -> list[str]:
    return [
        f"DESCRIBE_ONLY token={INTEGRATION_TOKEN}",
        "reuse the landed dual structured-collaboration executor envelope; do not add a registration command",
        "executor row #3 notes only: parsed=43, parents=17, field_atomic=26, residual_count_gap=0",
        "remote_validation=NOT_RUN; grants_release_credit=false; no preprod call is authorized here",
    ]
