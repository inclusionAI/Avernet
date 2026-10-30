"""Shared product-CLI contract for the Workspace detail-read slice."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError
from .workspace_cli_profile import (
    validate_workspace_cli_detail_profile,
)

WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-CLI-DETAIL-01"
REQUIREMENT_IDS = ("WORKSPACE.detail",)
CASE_METHOD = "execute_workspace_cli_create_detail"
FIXTURE_EFFECT = "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION"
SOURCE_FUNCTIONS = ("test_get_group_detail",)


@dataclass(frozen=True)
class WorkspaceCliDetailCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace CLI detail case is invalid")


def workspace_cli_detail_case_plan() -> WorkspaceCliDetailCasePlan:
    from pathlib import Path
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = read(root / "migration-contracts.json")
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == WORKSPACE_PARENT), None)
    if story is None:
        raise RunStateError("workspace CLI detail selected the wrong parent")
    requirement = next((row for row in story["requirements"]
                        if row["id"] == REQUIREMENT_IDS[0]), None)
    if (requirement is None
            or tuple(requirement["functions"]) != SOURCE_FUNCTIONS
            or story["migration_status"] != "NOT_MIGRATED"):
        raise RunStateError("workspace CLI detail source obligation changed")
    plan = WorkspaceCliDetailCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_cli_detail_adaptability() -> dict:
    plan = workspace_cli_detail_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["detail"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-group",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The detail-read product-CLI contract is implemented and "
            "mock-verified, but real Singlebox/preprod effect admission and "
            "execution remain independent gates."
        ),
    }


def validate_workspace_cli_detail_adapter(adapter: object) -> None:
    for name in ("create_retained_workspace_group", "read_workspace_group_detail"):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"workspace CLI detail adapter is missing {name}")


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
        raise RunStateError(f"workspace CLI detail evidence for {operation} is outside the contract")


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
        raise RunStateError("workspace CLI detail Group create output is not exact")


def _validate_detail(group_id: str, data: object, driver: str, member: str) -> None:
    participants = None
    if isinstance(data, dict) and isinstance(data.get("participants"), list):
        participants = [row.get("bot_uuid") for row in data["participants"]
                         if isinstance(row, dict)]
    if (not isinstance(data, dict)
            or data.get("id") != group_id
            or data.get("driver_bot") != driver
            or data.get("status") != "active"
            or not isinstance(participants, list)
            or len(participants or []) != 2
            or set(participants or []) != {driver, member}
            or not isinstance(data.get("group_kind"), str)
            or not data["group_kind"]
            or not isinstance(data.get("visibility"), str)
            or not data["visibility"]):
        raise RunStateError("workspace CLI detail readback is not exact")


def execute_workspace_cli_detail_case(adapter: object) -> CaseExecution:
    plan = workspace_cli_detail_case_plan()
    validate_workspace_cli_detail_adapter(adapter)
    error_type: str | None = None
    error_gap: str | None = None
    first_error_detail: str | None = None
    completed = 0
    records: list[dict] = []
    group_id = ""
    driver = getattr(adapter, "driver_id", "")
    member = getattr(adapter, "member_id", "")
    extracted: dict[str, object] = {}
    try:
        created = adapter.create_retained_workspace_group()
        created.validate()
        _validate_evidence(created, operation="detail.group.create")
        group_id = created.data.get("group_id", "")
        _validate_created(group_id, created.data, driver, member)
        records.append(created.evidence)

        detail = adapter.read_workspace_group_detail()
        detail.validate()
        _validate_evidence(detail, operation="detail.group.get")
        _validate_detail(group_id, detail.data, driver, member)
        extracted = detail.data
        records.append(detail.evidence)
        completed = 1
    except Exception as error:
        error_type = type(error).__name__
        error_gap = str(error) or None
        detail = record_error_detail(error)
        first_error_detail = detail

    data = {
        "group_id": group_id,
        "group_kind": extracted.get("group_kind") if isinstance(extracted, dict) else "",
        "visibility": extracted.get("visibility") if isinstance(extracted, dict) else "",
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


_CASE_CLASSES = {"detail.group.create": "create", "detail.group.get": "verify"}


class RemoteWorkspaceCliDetailAdapter:
    """Drive Group creation and its exact detailed read through product CLI."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_workspace_cli_detail_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Workspace CLI detail requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None

    def _call(self, operation: str, *, group_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("workspace product-CLI detail result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("workspace product-CLI detail evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = _CASE_CLASSES[operation]
        return AdapterCall(data=result["data"], evidence=evidence)

    def create_retained_workspace_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError("workspace CLI detail Group creation is a one-shot operation")
        result = self._call("detail.group.create")
        data = result.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or not isinstance(data.get("participants"), list)
                or len(data.get("participants") or []) != 2
                or set(data.get("participants") or []) != {self.driver_id, self.member_id}):
            raise RunStateError("workspace CLI detail Group binding differs")
        self.group_id = data.get("group_id")
        return result

    def read_workspace_group_detail(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("workspace CLI detail read must follow creation")
        return self._call("detail.group.get", group_id=self.group_id)
