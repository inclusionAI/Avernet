"""Reporting projections for the two Edge-permission backlog cases."""
from __future__ import annotations

from .case import (
    CASE_KEYS,
    SUITE,
    edge_permission_backlog_case_contract,
)

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-edge-permission-backlog-case-report-v1"

def case_summary() -> dict:
    rows = [edge_permission_backlog_case_contract(case_key) for case_key in CASE_KEYS]
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "suite": SUITE,
        "cases": [row.as_dict() for row in rows],
        "required_assertion_count": sum(row.required_assertion_count for row in rows),
        "status": "BLOCKED_INVENTORY",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }
