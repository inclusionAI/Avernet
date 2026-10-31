"""Mechanical density accounting and describe-only integration notes."""
from __future__ import annotations

from typing import Any

from .contract import authority_density_plan
from .defect import PREPROD_GAP_IDS

INTEGRATION_TOKEN = "AUTHORITY_DENSITY_42_OBLIGATIONS_20261031"


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
    accounting = authority_density_plan()["accounting"]
    return {
        "parsed": accounting["parsed_obligation_count"],
        "call_groups": accounting["call_group_count"],
        "shell_status_assertions": accounting["shell_status_assertions"],
        "shell_value_assertions": accounting["shell_value_assertions"],
        "residual_count_gap": accounting["residual_count_gap"],
    }


def md_table(receipt: dict[str, Any]) -> str:
    lines = ["| obligation | assertion | status | source |",
             "|---|---|---|---|"]
    for row in receipt["assertions"]:
        source = row["source"]
        lines.append(
            f"| {row['obligation_id']} | {row['assertion']} | {row['status']} | "
            f"{source['file']}:{source['line_start']}-{source['line_end']} |")
    return "\n".join(lines)


def proposed_executor_migration_notes() -> list[str]:
    return [
        f"DESCRIBE_ONLY token={INTEGRATION_TOKEN}",
        "reuse the existing dual-authority-run envelope; no new registration command",
        "executor row #18 (coverage-map story no 18; executor-migration.json rows "
        "index 35) notes only: parsed=42, call_groups=20, shell_status_assertions=20, "
        "shell_value_assertions=22, residual_count_gap=0",
        "preprod leg stays NOT_RUN with seven named admission gaps ("
        + ", ".join(PREPROD_GAP_IDS) + "); remote_validation=NOT_RUN; "
        "grants_release_credit=false",
    ]
