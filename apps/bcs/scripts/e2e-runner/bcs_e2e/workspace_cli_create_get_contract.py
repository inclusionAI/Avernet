"""Shared product-CLI contract for the trusted-team Workspace CLI obligations."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError
from .workspace_cli_profile import (
    validate_workspace_cli_profile,
)


WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-CLI-CREATE-GET-01"
REQUIREMENT_IDS = ("WORKSPACE.cli-create-get",)
CASE_METHOD = "execute_group_create_get_cli"
FIXTURE_EFFECT = "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION"
SOURCE_FUNCTIONS = ("test_group_create_via_cli", "test_group_get_via_cli")


@dataclass(frozen=True)
class WorkspaceCliCreateGetCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace CLI create/get case is invalid")


def workspace_cli_create_get_case_plan() -> WorkspaceCliCreateGetCasePlan:
    from pathlib import Path

    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = read(root / "migration-contracts.json")
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == WORKSPACE_PARENT), None)
    if story is None:
        raise RunStateError("workspace CLI selected the wrong parent")
    requirement = next((row for row in story["requirements"]
                        if row["id"] == REQUIREMENT_IDS[0]), None)
    if (requirement is None
            or tuple(requirement["functions"]) != SOURCE_FUNCTIONS
            or story["migration_status"] != "NOT_MIGRATED"):
        raise RunStateError("workspace CLI source obligation changed")
    plan = WorkspaceCliCreateGetCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_cli_create_get_adaptability() -> dict:
    plan = workspace_cli_create_get_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["cli-create-get"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-group",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The create/get product-CLI contract is implemented and mock-verified, but "
            "real Singlebox/preprod effect admission and execution remain independent gates."
        ),
    }


def validate_workspace_cli_create_get_adapter(adapter: object) -> None:
    for name in ("create_retained_workspace_group", "read_workspace_group"):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"workspace CLI adapter is missing {name}")


def _validate_evidence(result: AdapterCall, *, operation: str) -> None:
    evidence = result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_CLI"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "driver"
            or evidence.get("credential_kind") not in {"owned_local_bot_token", "human_cookie"}
            or evidence.get("fixture_effect") != FIXTURE_EFFECT
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(f"workspace CLI evidence for {operation} is outside the contract")


def execute_workspace_cli_create_get_case(adapter: object) -> CaseExecution:
    plan = workspace_cli_create_get_case_plan()
    validate_workspace_cli_create_get_adapter(adapter)
    error_type: str | None = None
    error_gap: str | None = None
    first_error_detail: str | None = None
    completed = 0
    records: list[dict] = []
    group_id = ""
    driver = getattr(adapter, "driver_id", "")
    member = getattr(adapter, "member_id", "")
    try:
        created = adapter.create_retained_workspace_group()
        created.validate()
        _validate_evidence(created, operation="group.create")
        data = created.data
        session_id = data.get("session_id") if isinstance(data, dict) else None
        if (not isinstance(data, dict)
                or data.get("driver_bot") != driver
                or data.get("participants") != [driver, member]
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or not isinstance(session_id, str)
                or not session_id.startswith(data["group_id"] + ":")):
            raise RunStateError("workspace CLI Group create output is not exact")
        group_id = data["group_id"]
        records.append(created.evidence)

        fetched = adapter.read_workspace_group()
        fetched.validate()
        _validate_evidence(fetched, operation="group.get")
        value = fetched.data
        participants = value.get("participants") if isinstance(value, dict) else None
        participant_ids = [
            row.get("bot_uuid") for row in participants
            if isinstance(participants, list) and isinstance(row, dict)
        ] if isinstance(participants, list) else None
        if (not isinstance(value, dict)
                or value.get("id") != group_id
                or value.get("driver_bot") != driver
                or value.get("status") != "active"
                or not isinstance(participant_ids, list)
                or len(participant_ids or []) != 2
                or set(participant_ids or []) != {driver, member}):
            raise RunStateError("workspace CLI Group readback is not exact")
        records.append(fetched.evidence)
        completed = 1
    except Exception as error:
        error_type = type(error).__name__
        error_gap = str(error) or None
        detail = record_error_detail(error)
        first_error_detail = detail

    data = {
        "group_id": group_id,
        "driver_actor_id": driver,
        "member_actor_id": member,
        "initial_session_bound": completed == 1,
        "group_retained": completed == 1,
    }
    evidence = {
        "transport": "PRODUCT_CLI",
        "fixture_effect": FIXTURE_EFFECT,
        "requests": records,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "group_id": group_id,
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


_CASE_CLASSES = {"group.create": "create", "group.get": "verify"}


class RemoteWorkspaceCliCreateGetAdapter:
    """Drive two exact Group leaves through the driver's product-CLI context."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_workspace_cli_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("workspace CLI create/get requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None

    def _call(self, operation: str, *, group_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("workspace product-CLI result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("workspace product-CLI evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = _CASE_CLASSES[operation]
        return AdapterCall(data=result["data"], evidence=evidence)

    def create_retained_workspace_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError("workspace Group creation is a one-shot operation")
        result = self._call("group.create")
        data = result.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or not isinstance(data.get("participants"), list)
                or len(data.get("participants") or []) != 2
                or set(data.get("participants") or []) != {self.driver_id, self.member_id}):
            raise RunStateError("workspace Group create returned the wrong participant binding")
        self.group_id = data.get("group_id")
        return result

    def read_workspace_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("workspace Group read must follow creation")
        return self._call("group.get", group_id=self.group_id)
