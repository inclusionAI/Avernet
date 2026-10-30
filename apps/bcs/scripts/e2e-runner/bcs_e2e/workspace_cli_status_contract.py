"""Shared product-CLI contract for the Workspace status/termination slice."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError


WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-CLI-STATUS-01"
REQUIREMENT_IDS = ("WORKSPACE.cli-status",)
CASE_METHOD = "execute_workspace_cli_status_and_termination"
FIXTURE_EFFECT = "RETAINED_DYNAMIC_GROUPS_AND_INITIAL_SESSIONS"
SOURCE_FUNCTIONS = ("test_group_status_via_cli", "test_group_terminate_via_cli")


@dataclass(frozen=True)
class WorkspaceCliStatusCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace CLI status case is invalid")


def workspace_cli_status_case_plan() -> WorkspaceCliStatusCasePlan:
    from pathlib import Path
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = read(root / "migration-contracts.json")
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == WORKSPACE_PARENT), None)
    if story is None:
        raise RunStateError("workspace CLI status selected the wrong parent")
    requirement = next((row for row in story["requirements"]
                        if row["id"] == REQUIREMENT_IDS[0]), None)
    if (requirement is None
            or tuple(requirement["functions"]) != SOURCE_FUNCTIONS
            or story["migration_status"] != "NOT_MIGRATED"):
        raise RunStateError("workspace CLI status source obligation changed")
    plan = WorkspaceCliStatusCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_cli_status_adaptability() -> dict:
    plan = workspace_cli_status_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["cli-status"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-group",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The status/termination product-CLI contract is implemented and "
            "mock-verified, but real Singlebox/preprod effect admission and "
            "execution remain independent gates."
        ),
    }


def validate_workspace_cli_status_adapter(adapter: object) -> None:
    methods = (
        "create_retained_completion_group", "read_completion_group_active",
        "update_completion_group_status", "read_completion_group_completed",
        "create_retained_termination_group", "read_termination_group_active",
        "terminate_termination_group", "read_termination_group_completed",
    )
    for name in methods:
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"workspace CLI status adapter is missing {name}")


def _validate_evidence(result: AdapterCall, *, operation: str) -> None:
    evidence = result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_CLI"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "driver"
            or evidence.get("credential_kind") not in {
                    "owned_local_bot_token", "owned_bot_token",
                    "human_cookie_with_owned_driver_bot_token"}
            or evidence.get("fixture_effect") != FIXTURE_EFFECT
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(f"workspace CLI status evidence for {operation} is outside the contract")


def _validate_created(group_id: str, data: object, driver: str, member: str) -> None:
    session_id = data.get("session_id") if isinstance(data, dict) else None
    if (not isinstance(data, dict)
            or data.get("driver_bot") != driver
            or not isinstance(data.get("participants"), list)
            or len(data.get("participants") or []) != 2
            or set(data.get("participants") or []) != {driver, member}
            or not isinstance(data.get("group_id"), str)
            or not data["group_id"] or data["group_id"] != group_id
            or not isinstance(session_id, str)
            or not session_id.startswith(group_id + ":")):
        raise RunStateError("workspace CLI status Group create output is not exact")


def _validate_read(group_id: str, data: object, driver: str, member: str,
                   status: str) -> None:
    participant_ids = None
    if isinstance(data, dict) and isinstance(data.get("participants"), list):
        participant_ids = [row.get("bot_uuid") for row in data["participants"]
                           if isinstance(row, dict)]
    if (not isinstance(data, dict) or data.get("id") != group_id
            or data.get("driver_bot") != driver or data.get("status") != status
            or not isinstance(participant_ids, list)
            or len(participant_ids or []) != 2
            or set(participant_ids or []) != {driver, member}):
        raise RunStateError("workspace CLI status Group readback is not exact")


def execute_workspace_cli_status_case(adapter: object) -> CaseExecution:
    plan = workspace_cli_status_case_plan()
    validate_workspace_cli_status_adapter(adapter)
    error_type: str | None = None
    error_gap: str | None = None
    first_error_detail: str | None = None
    completed = 0
    records: list[dict] = []
    completion_group = ""
    termination_group = ""
    driver = getattr(adapter, "driver_id", "")
    member = getattr(adapter, "member_id", "")
    try:
        created = adapter.create_retained_completion_group()
        created.validate()
        _validate_evidence(created, operation="status.group.create")
        completion_group = created.data.get("group_id", "")
        _validate_created(completion_group, created.data, driver, member)
        records.append(created.evidence)

        active = adapter.read_completion_group_active()
        active.validate()
        _validate_evidence(active, operation="status.group.read.active")
        _validate_read(completion_group, active.data, driver, member, "active")
        records.append(active.evidence)

        updated = adapter.update_completion_group_status()
        updated.validate()
        _validate_evidence(updated, operation="status.group.status")
        # Station update_group_status responds with updated/group_id/status/
        # reason/changed_by; assert the admission-relevant fields so an added
        # echo field cannot fake a PASS and a drift cannot hide as a crash.
        if (not isinstance(updated.data, dict)
                or updated.data.get("updated") is not True
                or updated.data.get("group_id") != completion_group
                or updated.data.get("status") != "completed"
                or updated.data.get("changed_by") != driver):
            raise RunStateError("workspace CLI status update result is not exact")
        records.append(updated.evidence)

        completed_read = adapter.read_completion_group_completed()
        completed_read.validate()
        _validate_evidence(completed_read, operation="status.group.read.completed")
        _validate_read(completion_group, completed_read.data, driver, member, "completed")
        records.append(completed_read.evidence)

        termination_created = adapter.create_retained_termination_group()
        termination_created.validate()
        _validate_evidence(termination_created, operation="termination.group.create")
        termination_group = termination_created.data.get("group_id", "")
        _validate_created(termination_group, termination_created.data, driver, member)
        records.append(termination_created.evidence)

        termination_active = adapter.read_termination_group_active()
        termination_active.validate()
        _validate_evidence(termination_active, operation="termination.group.read.active")
        _validate_read(termination_group, termination_active.data, driver, member, "active")
        records.append(termination_active.evidence)

        terminated = adapter.terminate_termination_group()
        terminated.validate()
        _validate_evidence(terminated, operation="termination.group.terminate")
        # Station terminate_group echoes the terminated Group plus a nested
        # session view; assert the admission-relevant fields only.
        if (not isinstance(terminated.data, dict)
                or terminated.data.get("terminated") is not True
                or terminated.data.get("group_id") != termination_group
                or terminated.data.get("status") != "completed"
                or terminated.data.get("terminated_by") != driver):
            raise RunStateError("workspace CLI terminate result is not exact")
        records.append(terminated.evidence)

        terminated_read = adapter.read_termination_group_completed()
        terminated_read.validate()
        _validate_evidence(terminated_read, operation="termination.group.read.completed")
        _validate_read(termination_group, terminated_read.data, driver, member, "completed")
        records.append(terminated_read.evidence)
        completed = 1
    except Exception as error:
        error_type = type(error).__name__
        error_gap = str(error) or None
        detail = record_error_detail(error)
        first_error_detail = detail

    data = {
        "completion_group_id": completion_group,
        "termination_group_id": termination_group,
        "driver_actor_id": driver,
        "member_actor_id": member,
        "completion_group_status": "completed",
        "termination_group_status": "completed",
        "groups_retained": completed == 1,
        "distinct_completion_and_termination_groups": (
            bool(completion_group and termination_group
                 and completion_group != termination_group)
        ),
    }
    evidence = {
        "transport": "PRODUCT_CLI",
        "fixture_effect": FIXTURE_EFFECT,
        "requests": records,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "completion_group_id": completion_group,
        "termination_group_id": termination_group,
    }
    step = StepOutcome(
        requirement_id=REQUIREMENT_IDS[0], method=CASE_METHOD,
        status="PASS" if error_type is None else "NOT_RUN",
        data=data if error_type is None else None,
        evidence=evidence if error_type is None else {},
        error_type=error_type,
        error_gap=error_gap,
        error_detail=first_error_detail,
    )
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
        error_detail=first_error_detail,
    )
    execution.validate()
    return execution
