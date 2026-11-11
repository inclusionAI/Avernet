"""Shared legacy-HTTP contract for the Workspace incident core slice.

The incident core exercises three caller kinds on one owned Group: the
driver Bot creates the incident response room, a dedicated QA Bot mutes
itself as a participant, and the owning Human finalizes the room with an
exact delete followed by a not-found read. Each credential is recorded
per request; the fixture is finalized, not retained.
"""

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
    WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION,
    WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION,
    validate_workspace_incident_core_profile,
)

WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-INCIDENT-CORE-01"
REQUIREMENT_IDS = (
    "WORKSPACE.incident-group",
    "WORKSPACE.participant-mode",
    "WORKSPACE.incident-delete",
)
CASE_METHODS = (
    "create_incident_response_group",
    "mute_incident_participant_mode",
    "finalize_incident_group_removal",
)
FIXTURE_EFFECT = "FINALIZED_BOUNDED_OWNED_BOTS_INCIDENT_CORE"
SOURCE_FUNCTIONS = (
    "_story_group_metadata_without_workspace",
    "_story_group_metadata_without_workspace",
    "_story_group_metadata_without_workspace",
)
STEP_OPERATIONS = {
    "WORKSPACE.incident-group": WORKSPACE_GROUP_CREATE_OPERATION,
    "WORKSPACE.participant-mode": WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION,
    "WORKSPACE.incident-delete": WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION,
}
INCIDENT_LABEL = "Incident response room"


@dataclass(frozen=True)
class WorkspaceIncidentCoreCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace incident core case is invalid")


def workspace_incident_core_case_plan() -> WorkspaceIncidentCoreCasePlan:
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
        raise RunStateError("workspace incident core selected the wrong parent")
    rows_by_id = {row["id"]: row for row in story["requirements"]}
    selected = {requirement_id: rows_by_id.get(requirement_id)
                for requirement_id in REQUIREMENT_IDS}
    for requirement_id in REQUIREMENT_IDS:
        row = selected[requirement_id]
        if row is None or row["functions"][0] != dict(zip(
                REQUIREMENT_IDS, SOURCE_FUNCTIONS))[requirement_id]:
            raise RunStateError("workspace incident core source obligation changed")
    if story["migration_status"] != "NOT_MIGRATED":
        raise RunStateError("workspace incident core parent is already migrated")
    plan = WorkspaceIncidentCoreCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_incident_core_adaptability() -> dict:
    plan = workspace_incident_core_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": [
            "incident_group", "participant_mode", "incident_delete"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-http-legacy-groups",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The legacy Group incident core HTTP contract is implemented "
            "and mock-verified, but real Singlebox or preprod effect "
            "admission and execution remain independent gates; the "
            "incident Group is finalized exactly, not retained."
        ),
    }


def validate_workspace_incident_core_adapter(adapter: object) -> None:
    for name in CASE_METHODS:
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(
                f"workspace incident core adapter is missing {name}")


def _validate_evidence(result: AdapterCall, *, operation: str,
                        case_class: str, credential_kind: str,
                        credential_kinds: list[str]) -> None:
    evidence = result.evidence
    requests = evidence.get("requests")
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_HTTP"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "case_owned_driver"
            or evidence.get("credential_kind") != credential_kind
            or evidence.get("credential_kinds") != credential_kinds
            or evidence.get("case_class") != case_class
            or evidence.get("fixture_effect") != FIXTURE_EFFECT
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or not isinstance(requests, list)
            or len(requests) != len(credential_kinds)):
        raise RunStateError(
            f"workspace incident core evidence for {case_class} "
            "is outside the contract")
    for record, kind in zip(requests, credential_kinds):
        if (not isinstance(record, dict)
                or record.get("method") not in {"GET", "POST", "PUT", "DELETE"}
                or not isinstance(record.get("path"), str)
                or not record["path"]
                or not isinstance(record.get("submitted_request_id"), str)
                or record.get("credential_kind") != kind):
            raise RunStateError(
                "workspace incident core request record is outside "
                "the contract")


def _validate_created(data: object, group_id: str, driver: str,
                      member: str, qa: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or not isinstance(group_id, str) or not group_id
            or data.get("driver_bot") != driver
            or data.get("participants") != [driver, member, qa]
            or data.get("label") != INCIDENT_LABEL
            or data.get("readback_verified") is not True):
        raise RunStateError(
            "workspace incident core creation did not acknowledge the "
            "exact incident Group")


def _detail_participants(data: object, group_id: str, driver: str,
                         member: str, qa: str) -> list[dict]:
    if (not isinstance(data, dict)
            or data.get("id") != group_id
            or data.get("driver_bot") != driver
            or data.get("label") != INCIDENT_LABEL
            or not isinstance(data.get("group_kind"), str)
            or not data["group_kind"]):
        raise RunStateError(
            "workspace incident core detail read-back differs from the "
            "created incident Group")
    rows = data.get("participants")
    if not isinstance(rows, list) or len(rows) != 3:
        raise RunStateError("workspace incident core roster is invalid")
    roles: dict[str, str] = {}
    for row in rows:
        if (not isinstance(row, dict)
                or not isinstance(row.get("bot_uuid"), str)
                or not isinstance(row.get("role"), str)):
            raise RunStateError(
                "workspace incident core participant view is invalid")
        roles[row["bot_uuid"]] = row["role"]
    if roles != {driver: "driver", member: "consultant", qa: "consultant"}:
        raise RunStateError(
            "workspace incident core roster differs from the created "
            "incident Group")
    return rows


def _validate_muted_readback(data: object, group_id: str, driver: str,
                     member: str, qa: str) -> None:
    rows = _detail_participants(data, group_id, driver, member, qa)
    muted = next((row for row in rows if row["bot_uuid"] == qa), None)
    if muted is None or muted.get("mode") != "muted":
        raise RunStateError(
            "workspace incident core self-mute was not read back on the "
            "exact participant")


def _validate_participant_mode_acknowledgement(
        data: object, group_id: str, qa: str) -> None:
    acknowledged = (data.get("data") if isinstance(data, dict) else None)
    if (not isinstance(data, dict)
            or data.get("success") is not True
            or not isinstance(acknowledged, dict)
            or acknowledged.get("group_id") != group_id
            or acknowledged.get("actor_id") != qa
            or acknowledged.get("mode") != "muted"):
        raise RunStateError(
            "workspace incident core participant mode did not acknowledge "
            "the exact self-mute")


def _validate_mute(data: object, group_id: str, qa: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or data.get("actor_id") != qa
            or data.get("mode") != "muted"
            or data.get("readback_verified") is not True):
        raise RunStateError(
            "workspace incident core self-mute was not recorded with "
            "the exact read-back")


def _validate_finalization_ack(data: object, group_id: str) -> None:
    if (not isinstance(data, dict)
            or data.get("deleted") is not True
            or data.get("id") != group_id):
        raise RunStateError(
            "workspace incident core deletion did not acknowledge the "
            "exact Group finalization")


def _validate_finalization(data: object, group_id: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or data.get("deleted") is not True
            or data.get("not_found_verified") is not True):
        raise RunStateError(
            "workspace incident core deletion did not acknowledge the "
            "exact Group finalization")


def execute_workspace_incident_core_case(adapter: object) -> CaseExecution:
    plan = workspace_incident_core_case_plan()
    validate_workspace_incident_core_adapter(adapter)
    preprod = adapter.profile["environment"]["tier"] == "preprod"
    human_credential = ("human_cookie" if preprod
                        else "owned_local_bot_token")
    driver_credential = adapter._credential_kind("driver")
    qa_credential = adapter._credential_kind("qa")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    for requirement_id, method_name in zip(REQUIREMENT_IDS, CASE_METHODS):
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={}, error_type=None,
                                      error_detail=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            result.validate()
            if requirement_id == "WORKSPACE.incident-group":
                _validate_evidence(
                    result, operation=WORKSPACE_GROUP_CREATE_OPERATION,
                    case_class="incident_group",
                    credential_kind=driver_credential,
                    credential_kinds=[driver_credential, human_credential])
                _validate_created(result.data,
                                  result.data.get("group_id", ""),
                                  adapter.driver_id, adapter.member_id,
                                  adapter.qa_id)
            elif requirement_id == "WORKSPACE.participant-mode":
                _validate_evidence(
                    result, operation=WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION,
                    case_class="participant_mode",
                    credential_kind=qa_credential,
                    credential_kinds=[qa_credential, human_credential])
                _validate_mute(result.data,
                                   result.data.get("group_id", ""),
                                   adapter.qa_id)
            else:
                _validate_evidence(
                    result, operation=WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION,
                    case_class="incident_delete",
                    credential_kind=human_credential,
                    credential_kinds=[human_credential, human_credential])
                _validate_finalization(result.data,
                                           result.data.get("group_id", ""))
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="PASS",
                                      data=result.data,
                                      evidence=dict(result.evidence),
                                      error_type=None,
                                      error_detail=None))
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
        error_type=error_type, ready_to_execute=error_type is None,
        error_detail=first_error_detail,
    )
    execution.validate()
    return execution


class RemoteWorkspaceIncidentCoreAdapter:
    """Drive the reviewed legacy Group incident core for one owned trio."""

    def __init__(self, profile: dict):
        self.profile = validate_workspace_incident_core_profile(profile)
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.qa_id = self.profile["actors"]["qa"]["bot_id"]
        self.group_id: str | None = None
        self.driver_client = WriteClient(
            self._bot_profile(self.profile["auth"]["driver_token_env"]))
        self.qa_client = WriteClient(
            self._bot_profile(self.profile["auth"]["qa_token_env"]))
        self.human_client = (
            WriteClient(self._cookie_profile())
            if self.profile["environment"]["tier"] == "preprod"
            else self.driver_client)

    @staticmethod
    def _entrypoint(profile: dict) -> dict:
        return {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }

    def _bot_profile(self, credential_ref: str) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        if self.profile["environment"]["tier"] == "preprod":
            # The station legacy face admits an owned Bot principal only as
            # the reviewed ACE duo: the owned Human Cookie session plus the
            # owned Bot Bearer token (live evidence ACE-BOT-TOKEN-BEARER; a
            # bare Bot Bearer returns USER_NOT_LOGIN / decisionBy=ACE).
            value["auth"] = {
                "kind": "human-cookie-with-owned-bot-token",
                "header_env": {"Authorization": credential_ref,
                               "Cookie": self.profile["auth"]["cookie_env"]},
                "authorization_scheme": "bearer",
            }
        else:
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {"Authorization": credential_ref},
            }
        value["entrypoint"] = self._entrypoint(self.profile)
        return value

    def _cookie_profile(self) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        value["auth"] = {
            "kind": "human-cookie",
            "header_env": {"Cookie": self.profile["auth"]["cookie_env"]},
        }
        value["entrypoint"] = self._entrypoint(self.profile)
        return value

    def _credential_kind(self, credential: str) -> str:
        if self.profile["environment"]["tier"] == "local":
            return "owned_local_bot_token"
        if credential == "human":
            return "human_cookie"
        return f"human_cookie_with_owned_{credential}_bot_token"

    def _evidence(self, *calls: tuple[dict, str], operation: str,
                    case_class: str) -> dict:
        requests = []
        credential_kinds = []
        for record, credential in calls:
            kind = self._credential_kind(credential)
            entry = dict(record)
            entry["credential_kind"] = kind
            requests.append(entry)
            credential_kinds.append(kind)
        evidence = {
            "transport": "PRODUCT_HTTP",
            "operation": operation,
            "caller_role": "case_owned_driver",
            "credential_kind": credential_kinds[0],
            "credential_kinds": credential_kinds,
            "case_class": case_class,
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "requests": requests,
        }
        writers = {id(self.driver_client): self.driver_client,
                   id(self.qa_client): self.qa_client,
                   id(self.human_client): self.human_client}
        secrets = [secret for writer in writers.values()
                   for secret in writer.headers.values()]
        if any(secret and secret in repr(evidence) for secret in secrets):
            raise RunStateError(
                "workspace incident core evidence must not expose a "
                "credential")
        return evidence

    def _require_group(self) -> str:
        if self.group_id is None:
            raise RunStateError(
                "workspace incident core requires its exact created "
                "incident Group")
        return self.group_id

    def create_incident_response_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError(
                "workspace incident core incident Group creation is a "
                "one-shot operation")
        data, record = self.driver_client.call(
            WORKSPACE_GROUP_CREATE_OPERATION,
            body={"driver_bot": self.driver_id,
                  "label": INCIDENT_LABEL,
                  "participants": [
                      {"bot_uuid": self.driver_id, "role": "driver"},
                      {"bot_uuid": self.member_id, "role": "consultant"},
                      {"bot_uuid": self.qa_id, "role": "consultant"}]},
            expected=(200,))
        if (not isinstance(data, dict)
                or not isinstance(data.get("id"), str) or not data["id"]
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id,
                                                self.member_id, self.qa_id]
                or not isinstance(data.get("group_kind"), str)
                or not data["group_kind"]
                or data.get("created") is not True):
            raise RunStateError(
                "workspace incident core creation did not acknowledge the "
                "exact incident Group")
        self.group_id = data["id"]
        detail, detail_record = self.human_client.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=self.group_id,
            expected=(200,))
        _detail_participants(detail, self.group_id, self.driver_id,
                             self.member_id, self.qa_id)
        return AdapterCall(
            data={"group_id": self.group_id, "driver_bot": self.driver_id,
                  "participants": [self.driver_id, self.member_id,
                                   self.qa_id],
                  "label": INCIDENT_LABEL, "readback_verified": True},
            evidence=self._evidence((record, "driver"),
                          (detail_record, "human"),
                          operation=WORKSPACE_GROUP_CREATE_OPERATION,
                          case_class="incident_group"),
        )

    def mute_incident_participant_mode(self) -> AdapterCall:
        group_id = self._require_group()
        muted, record = self.qa_client.call(
            WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION,
            group_id=group_id,
            body={"mode": "muted"},
            expected=(200,))
        _validate_participant_mode_acknowledgement(muted, group_id,
                                                       self.qa_id)
        detail, detail_record = self.human_client.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(200,))
        _validate_muted_readback(detail, group_id, self.driver_id,
                         self.member_id, self.qa_id)
        return AdapterCall(
            data={"group_id": group_id, "actor_id": self.qa_id,
                  "mode": "muted", "readback_verified": True},
            evidence=self._evidence((record, "qa"),
                          (detail_record, "human"),
                          operation=WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION,
                          case_class="participant_mode"),
        )

    def finalize_incident_group_removal(self) -> AdapterCall:
        group_id = self._require_group()
        deleted, record = self.human_client.call(
            WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION,
            group_id=group_id,
            expected=(200,))
        _validate_finalization_ack(deleted, group_id)
        gone, gone_record = self.human_client.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(404,))
        if gone is not None or gone_record.get("http_status") != 404:
            raise RunStateError(
                "workspace incident core finalization left the incident "
                "Group readable")
        return AdapterCall(
            data={"group_id": group_id, "deleted": True,
                  "not_found_verified": True},
            evidence=self._evidence((record, "human"),
                          (gone_record, "human"),
                          operation=WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION,
                          case_class="incident_delete"),
        )
