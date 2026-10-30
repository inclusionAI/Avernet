"""Shared legacy-HTTP contract for the Workspace group lifecycle slice."""

from __future__ import annotations

from dataclasses import dataclass
import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError
from .write_transport import WriteClient
from .workspace_cli_profile import (
    WORKSPACE_GROUP_CREATE_OPERATION,
    WORKSPACE_GROUP_DETAIL_OPERATION,
    WORKSPACE_GROUP_LIST_OPERATION,
    WORKSPACE_GROUP_MEMBER_ADD_OPERATION,
    WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION,
    WORKSPACE_GROUP_PATCH_OPERATION,
    validate_workspace_group_lifecycle_profile,
)

WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-GROUP-LIFECYCLE-01"
REQUIREMENT_IDS = (
    "WORKSPACE.create",
    "WORKSPACE.detail",
    "WORKSPACE.patch",
    "WORKSPACE.list",
    "WORKSPACE.members",
)
CASE_METHODS = (
    "create_owned_workspace_group",
    "read_owned_workspace_group_detail",
    "patch_owned_workspace_group",
    "list_owned_workspace_groups",
    "add_and_remove_workspace_member",
)
FIXTURE_EFFECT = "RETAINED_BOUNDED_OWNED_BOTS_GROUP_LIFECYCLE"
SOURCE_FUNCTIONS = (
    "test_create_group",
    "test_get_group_detail",
    "test_patch_group",
    "test_list_groups",
    "test_add_member",
)
STEP_OPERATIONS = {
    "WORKSPACE.create": WORKSPACE_GROUP_CREATE_OPERATION,
    "WORKSPACE.detail": WORKSPACE_GROUP_DETAIL_OPERATION,
    "WORKSPACE.patch": WORKSPACE_GROUP_PATCH_OPERATION,
    "WORKSPACE.list": WORKSPACE_GROUP_LIST_OPERATION,
    "WORKSPACE.members": WORKSPACE_GROUP_MEMBER_ADD_OPERATION,
}
PATCH_LABEL = "dual-group-lifecycle-e2e"
PATCH_CONTEXT = "dual-group-context-e2e"


@dataclass(frozen=True)
class WorkspaceGroupLifecycleCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace group lifecycle case is invalid")


def workspace_group_lifecycle_case_plan() -> WorkspaceGroupLifecycleCasePlan:
    from pathlib import Path

    from .migration_inventory import validate_contract
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = validate_contract(
        read(root / "migration-contracts.json"),
        source_root=root.parent / "e2e-test",
    )
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == WORKSPACE_PARENT), None)
    if story is None:
        raise RunStateError("workspace group lifecycle selected the wrong parent")
    rows_by_id = {row["id"]: row for row in story["requirements"]}
    selected = {requirement_id: rows_by_id.get(requirement_id)
                for requirement_id in REQUIREMENT_IDS}
    for requirement_id in REQUIREMENT_IDS:
        row = selected[requirement_id]
        if row is None or row["functions"][0] != dict(zip(
                REQUIREMENT_IDS, SOURCE_FUNCTIONS))[requirement_id]:
            raise RunStateError("workspace group lifecycle source obligation changed")
    if story["migration_status"] != "NOT_MIGRATED":
        raise RunStateError("workspace group lifecycle parent is already migrated")
    plan = WorkspaceGroupLifecycleCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_group_lifecycle_adaptability() -> dict:
    plan = workspace_group_lifecycle_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": [
            "create", "detail", "patch", "list", "members"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-http-legacy-groups",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The legacy Group lifecycle HTTP contract is implemented and "
            "mock-verified, but real Singlebox or preprod effect admission "
            "and execution remain independent gates; the created Group stays "
            "retained."
        ),
    }


def validate_workspace_group_lifecycle_adapter(adapter: object) -> None:
    for name in CASE_METHODS:
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(
                f"workspace group lifecycle adapter is missing {name}")


def _validate_evidence(result: AdapterCall, *, operation: str,
                        case_class: str) -> None:
    evidence = result.evidence
    requests = evidence.get("requests")
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_HTTP"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "case_owned_driver"
            or evidence.get("credential_kind")
                not in {"owned_local_bot_token", "human_cookie"}
            or evidence.get("case_class") != case_class
            or evidence.get("fixture_effect") != FIXTURE_EFFECT
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or not isinstance(requests, list) or not requests):
        raise RunStateError(
            f"workspace group lifecycle evidence for {case_class} "
            "is outside the contract")
    for record in requests:
        if (not isinstance(record, dict)
                or record.get("method") not in {"GET", "POST", "PATCH", "DELETE"}
                or not isinstance(record.get("path"), str)
                or not record["path"]
                or not isinstance(record.get("submitted_request_id"), str)):
            raise RunStateError(
                "workspace group lifecycle request record is outside the contract")


def _validate_created(data: object, group_id: str, driver: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or not isinstance(group_id, str) or not group_id
            or data.get("driver_bot") != driver
            or data.get("participants") != [driver]
            or data.get("retained") is not True):
        raise RunStateError("workspace group lifecycle create output is not exact")


def _validate_detail(data: object, group_id: str, driver: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or data.get("driver_bot") != driver
            or not isinstance(data.get("group_kind"), str)
            or not data["group_kind"]
            or not isinstance(data.get("visibility"), str)
            or not data["visibility"]
            or data.get("driver_participant") is not True
            or data.get("member_participant") is not False):
        raise RunStateError("workspace group lifecycle detail is not exact")


def _validate_patch(data: object, group_id: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or data.get("label") != PATCH_LABEL
            or data.get("context") != PATCH_CONTEXT
            or data.get("readback_verified") is not True):
        raise RunStateError("workspace group lifecycle patch is not exact")


def _validate_listing(data: object, group_id: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or data.get("bounded_list_match") is not True
            or type(data.get("total")) is not int
            or data["total"] < 1):
        raise RunStateError("workspace group lifecycle bounded list is not exact")


def _validate_membership(data: object, group_id: str, driver: str,
                          member: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or data.get("member_bot") != member
            or data.get("member_added_then_removed") is not True
            or data.get("final_participants") != [driver]):
        raise RunStateError(
            "workspace group lifecycle membership round trip is not exact")


def execute_workspace_group_lifecycle_case(adapter: object) -> CaseExecution:
    plan = workspace_group_lifecycle_case_plan()
    validate_workspace_group_lifecycle_adapter(adapter)
    driver = getattr(adapter, "driver_id", "")
    member = getattr(adapter, "member_id", "")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    for requirement_id, method_name in zip(REQUIREMENT_IDS, CASE_METHODS):
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={}, error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            result.validate()
            _validate_evidence(
                result, operation=STEP_OPERATIONS[requirement_id],
                case_class=_CASE_CLASSES[requirement_id],
            )
            if requirement_id == "WORKSPACE.create":
                _validate_created(result.data, result.data.get("group_id", ""), driver)
            elif requirement_id == "WORKSPACE.detail":
                _validate_detail(result.data, result.data.get("group_id", ""), driver)
            elif requirement_id == "WORKSPACE.patch":
                _validate_patch(result.data, result.data.get("group_id", ""))
            elif requirement_id == "WORKSPACE.list":
                _validate_listing(result.data, result.data.get("group_id", ""))
            else:
                _validate_membership(result.data, result.data.get("group_id", ""),
                                     driver, member)
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="PASS",
                                      data=result.data,
                                      evidence=dict(result.evidence),
                                      error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            detail = record_error_detail(error)
            if first_error_detail is None:
                first_error_detail = detail
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={},
                                      error_type=error_type,
                                      error_detail=detail))
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed,
        total_count=len(REQUIREMENT_IDS),
        error_type=error_type, error_detail=first_error_detail,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


_CASE_CLASSES = {
    "WORKSPACE.create": "create",
    "WORKSPACE.detail": "detail",
    "WORKSPACE.patch": "patch",
    "WORKSPACE.list": "list",
    "WORKSPACE.members": "membership",
}


class RemoteWorkspaceGroupLifecycleAdapter:
    """Drive the reviewed legacy Group lifecycle for one owned pair."""

    def __init__(self, profile: dict):
        self.profile = validate_workspace_group_lifecycle_profile(profile)
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.writer = WriteClient(self._http_profile(self.profile))

    @staticmethod
    def _http_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        if profile["environment"]["tier"] == "local":
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {
                    "Authorization": profile["auth"]["driver_token_env"],
                },
            }
        else:
            value["auth"] = {
                "kind": "human-cookie",
                "header_env": {"Cookie": profile["auth"]["cookie_env"]},
            }
        value["entrypoint"] = {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return value

    def _credential_kind(self) -> str:
        return ("owned_local_bot_token"
                if self.profile["environment"]["tier"] == "local"
                else "human_cookie")

    def _evidence(self, *records: dict, operation: str,
                    case_class: str) -> dict:
        evidence = {
            "transport": "PRODUCT_HTTP",
            "operation": operation,
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": case_class,
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "requests": [dict(record) for record in records],
        }
        secrets = list(self.writer.headers.values())
        if any(secret and secret in repr(evidence) for secret in secrets):
            raise RunStateError(
                "workspace group lifecycle evidence must not expose a credential")
        return evidence

    def _require_group(self) -> str:
        if self.group_id is None:
            raise RunStateError(
                "workspace group lifecycle requires its exact created Group")
        return self.group_id

    def create_owned_workspace_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError(
                "workspace group lifecycle Group creation is a one-shot operation")
        data, record = self.writer.call(
            WORKSPACE_GROUP_CREATE_OPERATION,
            body={"driver_bot": self.driver_id}, expected=(200,))
        if (not isinstance(data, dict)
                or not isinstance(data.get("id"), str) or not data["id"]
                or data.get("driver_bot") != self.driver_id
                or not isinstance(data.get("participants"), list)
                or data.get("participants") != [self.driver_id]
                or not isinstance(data.get("group_kind"), str)
                or not data["group_kind"]
                or data.get("created") is not True):
            raise RunStateError(
                "workspace group lifecycle creation did not acknowledge the "
                "exact owned Group")
        self.group_id = data["id"]
        return AdapterCall(
            data={"group_id": self.group_id, "driver_bot": self.driver_id,
                  "participants": [self.driver_id], "retained": True},
            evidence=self._evidence(record, operation=WORKSPACE_GROUP_CREATE_OPERATION,
                          case_class="create"),
        )

    def read_owned_workspace_group_detail(self) -> AdapterCall:
        group_id = self._require_group()
        data, record = self.writer.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(200,))
        participants = self._participants(data)
        if (not isinstance(data, dict)
                or data.get("id") != group_id
                or data.get("driver_bot") != self.driver_id
                or not isinstance(data.get("group_kind"), str)
                or not data["group_kind"]
                or not isinstance(data.get("visibility"), str)
                or not data["visibility"]
                or self.driver_id not in participants):
            raise RunStateError(
                "workspace group lifecycle detail did not read back the exact "
                "owned Group")
        return AdapterCall(
            data={"group_id": group_id, "driver_bot": self.driver_id,
                  "group_kind": data["group_kind"],
                  "visibility": data["visibility"],
                  "driver_participant": True, "member_participant":
                      self.member_id in participants},
            evidence=self._evidence(record, operation=WORKSPACE_GROUP_DETAIL_OPERATION,
                          case_class="detail"),
        )

    def patch_owned_workspace_group(self) -> AdapterCall:
        group_id = self._require_group()
        patched, record = self.writer.call(
            WORKSPACE_GROUP_PATCH_OPERATION, group_id=group_id,
            body={"name": PATCH_LABEL, "context": PATCH_CONTEXT},
            expected=(200,))
        if (not isinstance(patched, dict) or patched.get("name") != PATCH_LABEL):
            raise RunStateError(
                "workspace group lifecycle patch did not acknowledge the exact "
                "mutated fields")
        detail, detail_record = self.writer.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(200,))
        if (not isinstance(detail, dict)
                or detail.get("label") != PATCH_LABEL
                or detail.get("context") != PATCH_CONTEXT):
            raise RunStateError(
                "workspace group lifecycle patch read-back differs from the "
                "acknowledged patch")
        proof = self._participants(detail)
        if self.driver_id not in proof or len(proof) != 1:
            raise RunStateError(
                "workspace group lifecycle patch read-back changed the owned "
                "roster")
        return AdapterCall(
            data={"group_id": group_id, "label": PATCH_LABEL,
                  "context": PATCH_CONTEXT, "readback_verified": True},
            evidence=self._evidence(record, detail_record,
                          operation=WORKSPACE_GROUP_PATCH_OPERATION,
                          case_class="patch"),
        )

    def list_owned_workspace_groups(self) -> AdapterCall:
        group_id = self._require_group()
        data, record = self.writer.call(
            WORKSPACE_GROUP_LIST_OPERATION, expected=(200,))
        items = data.get("items") if isinstance(data, dict) else None
        if (not isinstance(items, list)
                or type(data.get("total")) is not int or data["total"] < 1):
            raise RunStateError(
                "workspace group lifecycle bounded list is outside the contract")
        matches = [row for row in items
                   if isinstance(row, dict) and row.get("id") == group_id]
        if (len(matches) != 1
                or matches[0].get("driver_bot") != self.driver_id):
            raise RunStateError(
                "workspace group lifecycle bounded list does not contain the "
                "exact newly created owned Group")
        return AdapterCall(
            data={"group_id": group_id, "bounded_list_match": True,
                  "total": data["total"], "offset": data.get("offset"),
                  "limit": data.get("limit")},
            evidence=self._evidence(record, operation=WORKSPACE_GROUP_LIST_OPERATION,
                          case_class="list"),
        )

    def add_and_remove_workspace_member(self) -> AdapterCall:
        group_id = self._require_group()
        added, add_record = self.writer.call(
            WORKSPACE_GROUP_MEMBER_ADD_OPERATION, group_id=group_id,
            body={"bot_uuid": self.member_id}, expected=(200,))
        if (not isinstance(added, dict) or added.get("added") is not True
                or added.get("session_id") != group_id
                or not isinstance(added.get("member"), dict)
                or added["member"].get("bot_uuid") != self.member_id):
            raise RunStateError(
                "workspace group lifecycle member addition did not acknowledge "
                "the exact member Bot")
        after_add, after_add_record = self.writer.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(200,))
        admitted = self._participants(after_add)
        if (self.member_id not in admitted
                or self.driver_id not in admitted
                or len(admitted) != 2):
            raise RunStateError(
                "workspace group lifecycle member addition was not read back "
                "in the exact roster")
        removed, remove_record = self.writer.call(
            WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION, group_id=group_id,
            expected=(200,))
        if (not isinstance(removed, dict) or removed.get("removed") is not True
                or removed.get("group_id") != group_id
                or removed.get("removed_bot_uuid") != self.member_id):
            raise RunStateError(
                "workspace group lifecycle member removal did not acknowledge "
                "the exact member Bot")
        after_remove, after_remove_record = self.writer.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(200,))
        final = self._participants(after_remove)
        if final != [self.driver_id]:
            raise RunStateError(
                "workspace group lifecycle member removal was not read back "
                "with the exact owned roster")
        return AdapterCall(
            data={"group_id": group_id, "member_bot": self.member_id,
                  "member_added_then_removed": True,
                  "final_participants": [self.driver_id]},
            evidence=self._evidence(
                add_record, after_add_record, remove_record, after_remove_record,
                operation=WORKSPACE_GROUP_MEMBER_ADD_OPERATION,
                case_class="membership"),
        )

    def _participants(self, data: object) -> list[str]:
        rows = data.get("participants") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RunStateError("workspace group lifecycle participants are invalid")
        participants = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("bot_uuid"), str):
                raise RunStateError("workspace group lifecycle participant is invalid")
            participants.append(row["bot_uuid"])
        return participants
