"""Shared product-CLI contract for the Workspace fusion slice."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError
from .workspace_cli_profile import (
    validate_workspace_cli_fuse_profile,
)

WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-CLI-FUSE-01"
REQUIREMENT_IDS = ("WORKSPACE.cli-fuse",)
CASE_METHOD = "execute_workspace_cli_fuse"
FIXTURE_EFFECT = "RETAINED_DYNAMIC_GROUP_INITIAL_SESSION_AND_FUSION"
SOURCE_FUNCTIONS = ("test_group_fuse_via_cli",)


@dataclass(frozen=True)
class WorkspaceCliFuseCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace CLI fusion case is invalid")


def workspace_cli_fuse_case_plan() -> WorkspaceCliFuseCasePlan:
    from pathlib import Path
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = read(root / "migration-contracts.json")
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == WORKSPACE_PARENT), None)
    if story is None:
        raise RunStateError("workspace CLI fusion selected the wrong parent")
    requirement = next((row for row in story["requirements"]
                        if row["id"] == REQUIREMENT_IDS[0]), None)
    if (requirement is None
            or tuple(requirement["functions"]) != SOURCE_FUNCTIONS
            or story["migration_status"] != "NOT_MIGRATED"):
        raise RunStateError("workspace CLI fusion source obligation changed")
    plan = WorkspaceCliFuseCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_cli_fuse_adaptability() -> dict:
    plan = workspace_cli_fuse_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["cli-fuse"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-group",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The fusion product-CLI contract is implemented and mock-verified, "
            "but real Singlebox/preprod effect admission and execution remain "
            "independent gates."
        ),
    }


def validate_workspace_cli_fuse_adapter(adapter: object) -> None:
    for name in ("create_retained_workspace_group", "fuse_workspace_group"):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"workspace CLI fusion adapter is missing {name}")


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
        raise RunStateError(f"workspace CLI fusion evidence for {operation} is outside the contract")


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
        raise RunStateError("workspace CLI fusion Group create output is not exact")


def _validate_fusion(group_id: str, data: object, driver: str, member: str) -> None:
    # Station fusion response (bcs-protocol FusionResponse plus the adapter
    # Group binding): exact key set, object arrays for perspectives/conflicts,
    # string arrays for alignment_points/key_insights, a non-empty
    # recommendation, and provider extra fusion_id/fusion_mode. A freshly
    # created Group carries cold participant context, so an empty
    # perspectives array is an admitted station result, not a failure.
    if (not isinstance(data, dict)
            or set(data) != {"group_id", "perspectives", "conflicts",
                             "alignment_points", "recommendation",
                             "key_insights", "extra"}
            or data.get("group_id") != group_id):
        raise RunStateError("workspace CLI fusion output is not bound to the Group")
    for key in ("perspectives", "conflicts"):
        if (not isinstance(data[key], list)
                or not all(isinstance(row, dict) for row in data[key])):
            raise RunStateError(f"workspace CLI fusion {key} is not an object array")
    for key in ("alignment_points", "key_insights"):
        if (not isinstance(data[key], list)
                or not all(isinstance(item, str) for item in data[key])):
            raise RunStateError(f"workspace CLI fusion {key} is not a string array")
    if (not isinstance(data["recommendation"], str) or not data["recommendation"]):
        raise RunStateError("workspace CLI fusion recommendation is not an exact non-empty string")
    extra = data["extra"]
    if (not isinstance(extra, dict)
            or not isinstance(extra.get("fusion_id"), str) or not extra["fusion_id"]
            or not isinstance(extra.get("fusion_mode"), str) or not extra["fusion_mode"]):
        raise RunStateError("workspace CLI fusion extra differs from the station result")
    perspectives = data["perspectives"]
    if perspectives:
        if ([row.get("bot_uuid") for row in perspectives] != [driver, member]
                or any(not isinstance(row.get("summary"), str) or not row["summary"]
                       for row in perspectives)
                or perspectives[0]["summary"] == perspectives[1]["summary"]):
            raise RunStateError("workspace CLI fusion warm perspectives are not exact")


def execute_workspace_cli_fuse_case(adapter: object) -> CaseExecution:
    plan = workspace_cli_fuse_case_plan()
    validate_workspace_cli_fuse_adapter(adapter)
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
        _validate_evidence(created, operation="fuse.group.create")
        group_id = created.data.get("group_id", "")
        _validate_created(group_id, created.data, driver, member)
        records.append(created.evidence)

        fused = adapter.fuse_workspace_group()
        fused.validate()
        _validate_evidence(fused, operation="fuse.group.fuse")
        _validate_fusion(group_id, fused.data, driver, member)
        extracted = dict(fused.data)
        records.append(fused.evidence)
        completed = 1
    except Exception as error:
        error_type = type(error).__name__
        error_gap = str(error) or None
        detail = record_error_detail(error)
        first_error_detail = detail

    fusion_perspectives = extracted.get("perspectives") if extracted else None
    data = {
        "group_id": group_id,
        "recommendation": extracted.get("recommendation") if extracted else "",
        "participant_bot_ids": [row.get("bot_uuid") for row in fusion_perspectives]
                              if isinstance(fusion_perspectives, list) else [],
        "perspectives_count": len(fusion_perspectives)
                              if isinstance(fusion_perspectives, list) else 0,
        "cold_context": not isinstance(fusion_perspectives, list) or not fusion_perspectives,
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


_CASE_CLASSES = {"fuse.group.create": "create", "fuse.group.fuse": "mutate"}


class RemoteWorkspaceCliFuseAdapter:
    """Create a retained Group and execute its exact product-CLI fusion."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_workspace_cli_fuse_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Workspace CLI fusion requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None

    def _call(self, operation: str, *, group_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("workspace product-CLI fusion result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("workspace product-CLI fusion evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = _CASE_CLASSES[operation]
        return AdapterCall(data=result["data"], evidence=evidence)

    def create_retained_workspace_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError("workspace CLI fusion Group creation is a one-shot operation")
        result = self._call("fuse.group.create")
        data = result.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or not isinstance(data.get("participants"), list)
                or len(data.get("participants") or []) != 2
                or set(data.get("participants") or []) != {self.driver_id, self.member_id}):
            raise RunStateError("workspace CLI fusion Group binding differs")
        self.group_id = data.get("group_id")
        return result

    def fuse_workspace_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("workspace CLI fusion must follow Group creation")
        result = self._call("fuse.group.fuse", group_id=self.group_id)
        if not isinstance(result.data, dict):
            raise RunStateError("workspace CLI fusion result is invalid")
        result.data["group_id"] = self.group_id
        return result

