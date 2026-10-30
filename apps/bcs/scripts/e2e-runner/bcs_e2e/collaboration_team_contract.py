"""Source-bound execution contract for the collaboration-team CLI Story."""

from __future__ import annotations

from typing import Protocol

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .local_operator_cli_contract import (
    COLLABORATION_TEAM_PARENT,
    COLLABORATION_TEAM_REQUIREMENTS,
    collaboration_team_source_binding,
)
from .run_state import RunStateError


CASE_ID = "DUAL-COLLABORATION-TEAM-CLI-01"
SUITE = "dual-collaboration-team-cli-v1"
CASE_METHOD = "execute_collaboration_team_cli_story"
REQUIREMENT_IDS = COLLABORATION_TEAM_REQUIREMENTS
RESOURCE_DISPOSITION = "REQUEST_AND_GROUP_HISTORY_RETAINED"
ADAPTER_METHODS = (
    "protect_customer_service_visibility",
    "request_reject_and_restore_customer_service_friendship",
    "create_and_read_release_group",
    "add_and_read_release_group_member",
    "compare_group_list_with_formal_api",
    "fuse_release_group",
    "complete_release_group",
    "terminate_release_group",
)


def collaboration_team_case_plan() -> dict:
    binding = collaboration_team_source_binding()
    requirement_ids = tuple(row["id"] for row in binding["requirements"])
    if (binding["parent"] != COLLABORATION_TEAM_PARENT
            or len(requirement_ids) != len(ADAPTER_METHODS)):
        raise RunStateError("collaboration-team case binding changed before execution")
    return {
        "case_id": CASE_ID,
        "parent_id": binding["parent"],
        "requirement_ids": list(requirement_ids),
        "adapter_methods": list(ADAPTER_METHODS),
        "source_revision": binding["source_revision"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def collaboration_team_adaptability() -> dict:
    plan = collaboration_team_case_plan()
    return {
        **plan,
        "selected_obligations": ["collaboration-team-full-parent"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_PARENT_EXECUTOR",
        "cli_surface": "product-bcs-cli-friend-visibility-group",
        "blockers": [
            "Preprod product-CLI admission and execution remain NOT_RUN; "
            "remote_validation must stay NOT_RUN until explicitly authorized.",
            "Owned-local execution and unified Singlebox evidence receive no "
            "release credit for the full 29-Story release.",
        ],
    }


def validate_collaboration_team_adapter(adapter: object) -> None:
    missing = [name for name in ADAPTER_METHODS
               if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError(
            "collaboration-team adapter is missing methods: " + ", ".join(missing))


def _validate_call(call: object, requirement_id: str) -> AdapterCall:
    if not isinstance(call, AdapterCall):
        raise RunStateError(f"collaboration-team obligation {requirement_id} did not return AdapterCall")
    call.validate()
    evidence = call.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") not in {"PRODUCT_CLI", "PRODUCT_HTTP"}
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(
            f"collaboration-team evidence for {requirement_id} is outside its contract")
    return call


def execute_collaboration_team_case(adapter: object) -> CaseExecution:
    plan = collaboration_team_case_plan()
    validate_collaboration_team_adapter(adapter)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    for requirement_id, method_name in zip(plan["requirement_ids"], ADAPTER_METHODS):
        if error_type is not None:
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={}, error_type=None,
                error_detail=None))
            continue
        try:
            call = _validate_call(getattr(adapter, method_name)(), requirement_id)
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="PASS", data=call.data, evidence=dict(call.evidence),
                error_type=None, error_detail=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            detail = record_error_detail(error)
            if first_error_detail is None:
                first_error_detail = detail
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={},
                error_type=error_type, error_detail=detail))
    execution = CaseExecution(
        case_id=CASE_ID,
        parent_id=plan["parent_id"],
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps),
        completed_count=completed,
        total_count=len(steps),
        error_type=error_type,
        ready_to_execute=error_type is None,
        error_detail=first_error_detail,
    )
    execution.validate()
    return execution


class CollaborationTeamAdapter(Protocol):
    def protect_customer_service_visibility(self) -> AdapterCall: ...
    def request_reject_and_restore_customer_service_friendship(self) -> AdapterCall: ...
    def create_and_read_release_group(self) -> AdapterCall: ...
    def add_and_read_release_group_member(self) -> AdapterCall: ...
    def compare_group_list_with_formal_api(self) -> AdapterCall: ...
    def fuse_release_group(self) -> AdapterCall: ...
    def complete_release_group(self) -> AdapterCall: ...
    def terminate_release_group(self) -> AdapterCall: ...
