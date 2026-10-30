"""Shared product-CLI contract for the Workspace bounded list slice."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError
from .workspace_cli_profile import (
    validate_workspace_cli_list_profile,
)


WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-CLI-LIST-01"
REQUIREMENT_IDS = ("WORKSPACE.list",)
CASE_METHOD = "execute_workspace_cli_create_list"
FIXTURE_EFFECT = "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION"
SOURCE_FUNCTIONS = ("test_list_groups",)


@dataclass(frozen=True)
class WorkspaceCliListCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace CLI list case is invalid")


def workspace_cli_list_case_plan() -> WorkspaceCliListCasePlan:
    from pathlib import Path
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = read(root / "migration-contracts.json")
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == WORKSPACE_PARENT), None)
    if story is None:
        raise RunStateError("workspace CLI list selected the wrong parent")
    requirement = next((row for row in story["requirements"]
                        if row["id"] == REQUIREMENT_IDS[0]), None)
    if (requirement is None
            or tuple(requirement["functions"]) != SOURCE_FUNCTIONS
            or story["migration_status"] != "NOT_MIGRATED"):
        raise RunStateError("workspace CLI list source obligation changed")
    plan = WorkspaceCliListCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_cli_list_adaptability() -> dict:
    plan = workspace_cli_list_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["list"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-group",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The bounded list product-CLI contract is implemented and "
            "mock-verified, but real Singlebox/preprod effect admission and "
            "execution remain independent gates."
        ),
    }


def validate_workspace_cli_list_adapter(adapter: object) -> None:
    for name in ("create_retained_workspace_group", "list_workspace_groups"):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"workspace CLI list adapter is missing {name}")


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
        raise RunStateError(f"workspace CLI list evidence for {operation} is outside the contract")


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
        raise RunStateError("workspace CLI list Group create output is not exact")


def _validate_list(group_id: str, data: object, driver: str, member: str) -> None:
    items = data.get("items") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or not isinstance(items, list)
            or type(data.get("offset")) is not int or data["offset"] != 0
            or type(data.get("returned")) is not int or data["returned"] <= 0
            or data["returned"] != len(items)
            or type(data.get("total")) is not int or data["total"] < data["returned"]
            or type(data.get("has_more")) is not bool):
        raise RunStateError("workspace CLI list pagination result is not exact")
    # Station legacy list projection (bcs-http bot_group_list_entry_to_legacy_json):
    # group_id/coordinator_bot/participants[{bot_uuid}] without status/id keys,
    # and the authenticated Bot token resolves the actor domain on preprod.
    exact = []
    for row in items:
        if not isinstance(row, dict):
            continue
        row_id = row.get("group_id")
        if not isinstance(row_id, str):
            row_id = row.get("id")
        if row_id == group_id:
            exact.append(row)
    if len(exact) != 1:
        raise RunStateError("workspace CLI list did not contain the exact newly created Group")
    row = exact[0]
    coordinator = row.get("coordinator_bot")
    if not isinstance(coordinator, str):
        coordinator = row.get("driver_bot")
    participant_ids = {
        item.get("bot_uuid") for item in row.get("participants", [])
        if isinstance(item, dict)
    }
    if (coordinator != driver or not {driver, member} <= participant_ids):
        raise RunStateError("workspace CLI list Group projection is not exact")
    # The active-status obligation is proven by the get-group leaf, which the
    # station projects with an explicit status field (see the detail slice).


def execute_workspace_cli_list_case(adapter: object) -> CaseExecution:
    plan = workspace_cli_list_case_plan()
    validate_workspace_cli_list_adapter(adapter)
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
        _validate_evidence(created, operation="list.group.create")
        group_id = created.data.get("group_id", "")
        _validate_created(group_id, created.data, driver, member)
        records.append(created.evidence)

        listed = adapter.list_workspace_groups()
        listed.validate()
        _validate_evidence(listed, operation="list.group.list")
        _validate_list(group_id, listed.data, driver, member)
        records.append(listed.evidence)
        completed = 1
    except Exception as error:
        error_type = type(error).__name__
        error_gap = str(error) or None
        detail = record_error_detail(error)
        first_error_detail = detail

    data = {
        "group_id": group_id,
        "bounded_group_returned": completed == 1,
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


# Shared adapter for the bounded list obligation (consolidated from
# remote_workspace_cli_list_adapter.py; file-budget consolidation).
_CASE_CLASSES = {"list.group.create": "create", "list.group.list": "verify"}


class RemoteWorkspaceCliListAdapter:
    """Drive Group creation and a bounded current-actor list through CLI."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_workspace_cli_list_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Workspace CLI list requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None

    def _call(self, operation: str, *, group_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("workspace product-CLI list result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("workspace product-CLI list evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = _CASE_CLASSES[operation]
        return AdapterCall(data=result["data"], evidence=evidence)

    def create_retained_workspace_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError("workspace CLI list Group creation is a one-shot operation")
        result = self._call("list.group.create")
        data = result.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or not isinstance(data.get("participants"), list)
                or len(data.get("participants") or []) != 2
                or set(data.get("participants") or []) != {self.driver_id, self.member_id}):
            raise RunStateError("workspace CLI list Group binding differs")
        self.group_id = data.get("group_id")
        return result

    def list_workspace_groups(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("workspace CLI list must follow Group creation")
        return self._call("list.group.list")
