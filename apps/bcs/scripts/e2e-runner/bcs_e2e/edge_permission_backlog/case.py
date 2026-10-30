"""Contracts for the two backlog Edge-permission TestCase slices.

Each case reuses the frozen :mod:`~bcs_e2e.edge_friend_mutation_case` plan and
the local remote placeholders from :mod:`~bcs_e2e.local_edge_backlog`.  The
only new material here is the dual admission envelope: it keeps the local and
preprod outcomes separate, never allowing a local model result to masquerade
as a preprod execution.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..edge_friend_mutation_case import (
    BACKLOG_CASE_IDS,
    edge_friend_mutation_case_plan,
)
from ..local_edge_backlog import (
    DEPT_CASE_ID as DEPT_SOURCE_CASE_ID,
    DEPT_PARENT,
    TC_CASE_ID,
    TC_PARENT,
)
from ..profile import ConfigError

CASE_KEYS = ("tc-friend-auth-sync", "dept-scope-friend-ext")
CASE_IDS = (TC_CASE_ID, DEPT_SOURCE_CASE_ID)
PARENT_IDS = (TC_PARENT, DEPT_PARENT)
SUITE = "dual-edge-permission-backlog-v1"


@dataclass(frozen=True)
class EdgePermissionBacklogCase:
    case_key: str
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    environment: str
    case_version: int
    required_assertion_count: int

    def validate(self) -> None:
        if (self.case_key not in CASE_KEYS
                or self.case_id not in CASE_IDS
                or self.parent_id not in PARENT_IDS
                or self.environment not in {"LOCAL_MODEL_READY", "REMOTE_PLACEHOLDER"}
                or self.case_version != 1
                or not self.requirement_ids
                or len(self.requirement_ids) != self.required_assertion_count):
            raise ConfigError("Edge-permission backlog case contract is invalid")

    def as_dict(self) -> dict:
        self.validate()
        return {
            "case_key": self.case_key,
            "case_id": self.case_id,
            "case_version": self.case_version,
            "parent_id": self.parent_id,
            "requirement_ids": list(self.requirement_ids),
            "required_assertion_count": self.required_assertion_count,
            "environment": self.environment,
            "suite": SUITE,
        }


def edge_permission_backlog_case_contract(case_key: str) -> EdgePermissionBacklogCase:
    if case_key not in CASE_KEYS:
        raise ConfigError("Edge-permission backlog case must select one of the two backlog cases")
    case_id = CASE_IDS[CASE_KEYS.index(case_key)]
    plan = edge_friend_mutation_case_plan(case_id)
    parent_id = plan.parent_id
    if parent_id not in PARENT_IDS or case_id not in BACKLOG_CASE_IDS:
        raise ConfigError("Edge-permission backlog case contract drifted from the frozen source")
    return EdgePermissionBacklogCase(
        case_key=case_key,
        case_id=case_id,
        parent_id=parent_id,
        requirement_ids=tuple(plan.requirement_ids),
        environment="LOCAL_MODEL_READY",
        case_version=1,
        required_assertion_count=len(plan.requirements),
    )


def dual_case_adaptability() -> dict:
    cases = [edge_permission_backlog_case_contract(case_key) for case_key in CASE_KEYS]
    return {
        "schema_version": 1,
        "suite": SUITE,
        "cases": [row.as_dict() for row in cases],
        "totals": {
            "case_count": len(cases),
            "required_assertion_count": sum(row.required_assertion_count for row in cases),
        },
        "environments": {
            "owned-local": "LOCAL_MODEL_READY",
            "preprod": "NOT_RUN",
        },
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }
