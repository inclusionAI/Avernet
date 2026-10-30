"""Mechanical accounting and describe-only integration text."""
from __future__ import annotations

from typing import Any

from .contract import group_context_density_plan

INTEGRATION_TOKEN = "GROUP_CONTEXT_DENSITY_9_OBLIGATIONS_20261031"


def density_accounting(receipt: dict[str, Any]) -> dict[str, Any]:
    rows = receipt["assertions"]
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("PASS", "FAIL", "NOT_RUN")}
    expected = receipt["case_scope"]["parsed_obligation_count"]
    return {
        "counts": counts,
        "expected": expected,
        "observed": len(rows),
        "residual_count_gap": expected - len(rows),
        "grants_release_credit": receipt["grants_release_credit"],
    }


def family_accounting() -> dict[str, int]:
    accounting = group_context_density_plan()["accounting"]
    return {
        "parsed": accounting["parsed_obligation_count"],
        "parents": accounting["parent_obligation_count"],
        "field_atomic": accounting["field_atomic_count"],
        "residual_count_gap": accounting["residual_count_gap"],
    }


def md_table(receipt: dict[str, Any]) -> str:
    lines = ["| obligation | level | status | source |", "|---|---|---|---|"]
    for row in receipt["assertions"]:
        source = row["source"]
        lines.append(
            f"| {row['obligation_id']} | {row['level']} | {row['status']} | "
            f"{source['file']}:{source['line_start']}-{source['line_end']} |")
    return "\n".join(lines)


def proposed_executor_migration_notes() -> list[str]:
    return [
        f"DESCRIBE_ONLY token={INTEGRATION_TOKEN}",
        "reuse the existing dual-group-context-run envelope; no new registration command",
        "executor row #17 notes only: parsed=18, parents=9, field_atomic=9, residual_count_gap=0",
        "r7 batch roster reuses dual-group-context-run; remote_validation=NOT_RUN; grants_release_credit=false",
    ]
