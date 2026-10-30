"""Shared product-CLI contract for the first Session-file core slice."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .file_transport import RemoteFileClient
from .write_transport import WriteClient
from .run_state import RunStateError
from .local_session_contract import PARENT, session_source_binding
from .workspace_cli_runner import SESSION_CLI_CREATE_GET_EFFECT
from .workspace_cli_profile import (
    SESSION_FILE_CORE_PROFILE_CONTRACT,
    validate_session_file_core_profile,
)
from .session_cli_profile import (
    SESSION_CLI_BOT_GROUPS_OPERATION,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    validate_session_cli_create_get_profile,
)

SESSION_FILE_PARENT = "story_session_file_workspace"
CASE_ID = "DUAL-SESSION-FILE-CORE-01"
REQUIREMENT_IDS = (
    "CLI-FILE.setup", "CLI-FILE.upload", "CLI-FILE.list", "CLI-FILE.capabilities",
    "CLI-FILE.metadata", "CLI-FILE.download", "CLI-FILE.share",
    "CLI-FILE.shared-metadata",
    "CLI-FILE.shared-content", "CLI-FILE.delete",
)
# The upstream story has twelve parsed assertion points.  The three combined
# source obligations above are split at execution time without changing the
# frozen source-binding IDs: status and exact-id are separate predicates for
# metadata, status and session-hiding are separate for shared metadata, and the
# shared-content body is validated byte-for-byte.
ASSERTION_IDS = (
    "CLI-FILE.setup", "CLI-FILE.upload", "CLI-FILE.list", "CLI-FILE.capabilities",
    "CLI-FILE.metadata-status", "CLI-FILE.metadata-file-id",
    "CLI-FILE.download", "CLI-FILE.share",
    "CLI-FILE.shared-metadata-status",
    "CLI-FILE.shared-metadata-hides-session",
    "CLI-FILE.shared-content-bytes", "CLI-FILE.delete",
)
ASSERTION_METHODS = {
    "CLI-FILE.setup": "setup_session_file_workspace",
    "CLI-FILE.upload": "upload_session_file",
    "CLI-FILE.list": "list_session_files",
    "CLI-FILE.capabilities": "read_session_file_capabilities",
    "CLI-FILE.metadata-status": "read_session_file_metadata",
    "CLI-FILE.metadata-file-id": "read_session_file_metadata",
    "CLI-FILE.download": "download_session_file",
    "CLI-FILE.share": "share_session_file",
    "CLI-FILE.shared-metadata-status": "read_shared_session_file_metadata",
    "CLI-FILE.shared-metadata-hides-session": "read_shared_session_file_metadata",
    "CLI-FILE.shared-content-bytes": "download_shared_session_file",
    "CLI-FILE.delete": "delete_session_file",
}
CASE_METHODS = {
    "CLI-FILE.setup": "setup_session_file_workspace",
    "CLI-FILE.upload": "upload_session_file",
    "CLI-FILE.list": "list_session_files",
    "CLI-FILE.capabilities": "read_session_file_capabilities",
    "CLI-FILE.metadata": "read_session_file_metadata",
    "CLI-FILE.download": "download_session_file",
    "CLI-FILE.share": "share_session_file",
    "CLI-FILE.shared-metadata": "read_shared_session_file_metadata",
    "CLI-FILE.shared-content": "download_shared_session_file",
    "CLI-FILE.delete": "delete_session_file",
}
FIXTURE_EFFECT = "RETAINED_DYNAMIC_GROUP_SESSIONS_AND_FILE"
DELETE_FILE_EFFECT = "RETAINED_DYNAMIC_GROUP_SESSIONS_DELETED_FILE"
SOURCE_FUNCTIONS = ("story_session_file_workspace",)
FILE_SIZE = 29
FILE_BYTES = b"bcs-e2e-session-file-payload\n"
FILE_NAME = "bcs-e2e-session-file-source.txt"


@dataclass(frozen=True)
class SessionFileCoreCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != SESSION_FILE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("Session-file core case is invalid")


def session_file_core_case_plan() -> SessionFileCoreCasePlan:
    from pathlib import Path
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = read(root / "migration-contracts.json")
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == SESSION_FILE_PARENT), None)
    if story is None or story["migration_status"] != "NOT_MIGRATED":
        raise RunStateError("Session-file core selected the wrong parent")
    rows = [row for row in story["requirements"] if row["id"] in REQUIREMENT_IDS]
    if ([row["id"] for row in rows] != list(REQUIREMENT_IDS)
            or any(tuple(row["functions"]) != SOURCE_FUNCTIONS for row in rows)):
        raise RunStateError("Session-file core source obligations changed")
    plan = SessionFileCoreCasePlan(
        case_id=CASE_ID, parent_id=SESSION_FILE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def session_file_core_adaptability() -> dict:
    plan = session_file_core_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "assertion_ids": list(ASSERTION_IDS),
        "assertion_count": len(ASSERTION_IDS),
        "selected_obligations": ["setup", "upload", "list", "capabilities",
                               "metadata-status", "metadata-file-id", "download",
                               "share", "shared-metadata-status",
                               "shared-metadata-hides-session",
                               "shared-content-bytes", "delete"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session-file",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The first Session-file core contract is implemented and "
            "mock-verified, but real Singlebox/preprod effect admission and "
            "execution remain independent gates."
        ),
    }


def _session_file_metadata_profile(profile: dict) -> dict:
    if profile["environment"]["tier"] == "local":
        auth = {"header_env": {
            "Authorization": profile["auth"]["driver_token_env"],
        }}
    else:
        auth = {"header_env": {
            "Cookie": profile["auth"]["cookie_env"],
        }}
    return {
        "environment": copy.deepcopy(profile["environment"]),
        "entrypoint": {"kind": profile["entrypoint"]["kind"],
                      "origin": profile["entrypoint"]["origin"]},
        "auth": auth,
        "file_transfer": copy.deepcopy(profile.get("file_transfer", {})),
        "policy": copy.deepcopy(profile["policy"]),
    }


def validate_session_file_core_adapter(adapter: object) -> None:
    for name in CASE_METHODS.values():
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"Session-file core adapter is missing {name}")


def _validated_request(result: AdapterCall, *, operation: str,
                        credential_kind: str, transport: str = "PRODUCT_CLI",
                        caller_role: str = "driver",
                        fixture_effect: str = FIXTURE_EFFECT) -> dict:
    evidence = result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != transport
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != caller_role
            or evidence.get("credential_kind") != credential_kind
            or evidence.get("fixture_effect") != fixture_effect
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(f"Session-file core evidence for {operation} is invalid")
    return evidence


class RemoteSessionFileCoreAdapter:
    """Create the retained Session and begin its bounded CLI-file lifecycle."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_file_core_profile(profile)
        if (self.profile["contract"] != SESSION_FILE_CORE_PROFILE_CONTRACT
                or not callable(getattr(runner, "execute", None))):
            raise RunStateError("Session-file core requires its product-CLI runner")
        self.runner = runner
        self.file_client = RemoteFileClient(
            _session_file_metadata_profile(self.profile))

        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.initial_session_id = ""
        self.session_id: str | None = None
        self.file_id: str | None = None
        self.share_token: str | None = None
        self.share_url: str | None = None
        self.share_expires_at: int | None = None

    def _call(self, operation: str, *, group_id: str | None = None,
              session_id: str | None = None,
              file_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id,
                                    session_id=session_id, file_id=file_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("Session-file product-CLI result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("Session-file product-CLI evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["operation"] = operation
        return AdapterCall(data=result["data"], evidence=evidence)

    def setup_session_file_workspace(self) -> AdapterCall:
        if self.group_id is not None or self.session_id is not None:
            raise RunStateError("Session-file setup is a one-shot operation")
        created = self._call("session-file.group.create")
        created.validate()
        data = created.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or not isinstance(data.get("session_id"), str)):
            raise RunStateError("Session-file Group setup returned an inexact binding")
        self.group_id = data["group_id"]
        self.initial_session_id = data["session_id"]

        session = self._call("session-file.session.create", group_id=self.group_id)
        session.validate()
        value = session.data
        if (not isinstance(value, dict)
                or not isinstance(value.get("session_id"), str)
                or not value["session_id"].startswith(self.group_id + ":")
                or value.get("session_kind") != "chat"
                or value.get("status") not in ("active", "running")):
            raise RunStateError("Session-file explicit Session create is not exact")
        self.session_id = value["session_id"]
        evidence = {
            "transport": "PRODUCT_CLI",
            "fixture_effect": FIXTURE_EFFECT,
            "requests": [created.evidence, session.evidence],
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "group_id": self.group_id,
            "session_id": self.session_id,
        }
        return AdapterCall(data={**data, "explicit_session": value}, evidence=evidence)

    def upload_session_file(self) -> AdapterCall:
        if self.session_id is None or self.file_id is not None:
            raise RunStateError("Session-file upload must follow setup exactly once")
        result = self._call("session-file.upload", session_id=self.session_id)
        result.validate()
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("file_id"), str) or not data["file_id"]
                or data.get("file_name") != FILE_NAME
                or data.get("size") != FILE_SIZE
                or data.get("status") not in ("ready", "Ready")):
            raise RunStateError("Session-file upload result is not exact")
        self.file_id = data["file_id"]
        return result

    def list_session_files(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file list must follow upload")
        result = self._call("session-file.list", session_id=self.session_id)
        result.validate()
        data = result.data
        items = data.get("items") if isinstance(data, dict) else None
        item = items[0] if isinstance(items, list) and len(items) == 1 else None
        if (not isinstance(data, dict) or data.get("total") != 1
                or not isinstance(item, dict)
                or item.get("file_id") != self.file_id
                or item.get("file_name") != FILE_NAME
                or item.get("size") != FILE_SIZE
                or item.get("status") not in ("ready", "Ready")):
            raise RunStateError("Session-file list result is not exact")
        return result

    def read_session_file_capabilities(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file capabilities must follow upload")
        result = self._call("session-file.capabilities", session_id=self.session_id)
        result.validate()
        data = result.data
        expected_storage = "local" if self.profile["environment"]["tier"] == "local" else "baas"
        if (not isinstance(data, dict)
                or data.get("storage") != expected_storage
                or not isinstance((max_size := data.get("max_file_size_bytes",
                                                        data.get("max_size", 0))), int)
                or max_size <= 0
                or data.get("file_uploads", data.get("presign_upload")) is not True):
            raise RunStateError("Session-file capabilities are not environment-exact")
        return result

    def read_session_file_metadata(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file metadata must follow upload")
        data, record = self.file_client.metadata(self.session_id, self.file_id)
        if (not isinstance(data, dict) or data.get("file_id") != self.file_id
                or not isinstance(record, dict)
                or record.get("operation") != "file.metadata"
                or record.get("http_status") != 200
                or not isinstance(record.get("path"), str)):
            raise RunStateError("Session-file metadata result is not exact")
        credential_kind = ("owned_local_bot_token"
                           if self.profile["environment"]["tier"] == "local"
                           else "human_cookie_with_owned_driver_bot_token")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": credential_kind,
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def read_shared_session_file_metadata(self) -> AdapterCall:
        if self.share_token is None or self.file_id is None:
            raise RunStateError("Session-file shared metadata must follow share")
        data, record = self.file_client.shared_metadata(self.share_token)
        if (not isinstance(data, dict)
                or data.get("file_id") != self.file_id
                or data.get("file_name") != FILE_NAME
                or data.get("size") != FILE_SIZE
                or data.get("status") != "Ready"
                or "session_id" in data
                or not isinstance(record, dict)
                or record.get("operation") != "file.shared_metadata"
                or record.get("http_status") != 200
                or not isinstance(record.get("path"), str)):
            raise RunStateError("Session-file shared metadata result is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "public_share",
            "credential_kind": "share_token",
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def download_shared_session_file(self) -> AdapterCall:
        if self.share_token is None or self.file_id is None:
            raise RunStateError("Session-file shared-content must follow share")
        body, record = self.file_client.shared_download(self.share_token)
        if (body != FILE_BYTES
                or not isinstance(record, dict)
                or record.get("operation") != "file.shared_download"
                or record.get("http_status") != 200
                or not isinstance(record.get("path"), str)):
            raise RunStateError("Session-file shared-content result is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "public_share",
            "credential_kind": "share_token",
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data={
            "size": FILE_SIZE,
            "sha256": hashlib.sha256(FILE_BYTES).hexdigest(),
        }, evidence=evidence)

    def download_session_file(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file download must follow upload")
        result = self._call("session-file.download", session_id=self.session_id,
                            file_id=self.file_id)
        result.validate()
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("out"), str)
                or data["out"] != str(self.runner.session_file_download)):
            raise RunStateError("Session-file download target is not exact")
        if self.runner.session_file_download.read_bytes() != FILE_BYTES:
            raise RunStateError("Session-file download bytes differ from upload")
        return AdapterCall(data={
            "out": data["out"], "size": FILE_SIZE,
            "sha256": hashlib.sha256(FILE_BYTES).hexdigest(),
        }, evidence=result.evidence)

    def share_session_file(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file share must follow upload")
        result = self._call("session-file.share", session_id=self.session_id,
                            file_id=self.file_id)
        result.validate()
        data = result.data
        token = data.get("share_token") if isinstance(data, dict) else None
        url = data.get("share_url") if isinstance(data, dict) else None
        expires = data.get("expires_at") if isinstance(data, dict) else None
        if (not isinstance(token, str) or not token
                or not isinstance(url, str) or not url
                or not isinstance(expires, int) or expires <= 0):
            raise RunStateError("Session-file share result is not exact")
        self.share_token = token
        self.share_url = url
        self.share_expires_at = expires
        self.file_client.register_share_target(token, url)
        return AdapterCall(data={
            "share_granted": True, "share_token_present": True,
            "expires_at": expires,
        }, evidence=result.evidence)

    def delete_session_file(self) -> AdapterCall:
        if (self.session_id is None or self.file_id is None
                or self.share_token is None):
            raise RunStateError("Session-file delete must follow shared content")
        result = self._call("session-file.delete", session_id=self.session_id,
                            file_id=self.file_id)
        result.validate()
        if result.data not in (None, {"deleted": True}):
            raise RunStateError("Session-file delete acknowledgement is not exact")
        invalidations = []
        _, file_absence = self.file_client.verify_deleted(self.session_id, self.file_id)
        invalidations.append({
            "transport": "PRODUCT_HTTP", "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie_with_owned_driver_bot_token"),
            "fixture_effect": DELETE_FILE_EFFECT, **file_absence,
        })
        _, shared_metadata_absence = self.file_client.shared_metadata_verify_deleted(self.share_token)
        invalidations.append({
            "transport": "PRODUCT_HTTP", "caller_role": "public_share",
            "credential_kind": "share_token", "fixture_effect": DELETE_FILE_EFFECT,
            **shared_metadata_absence,
        })
        _, shared_content_absence = self.file_client.verify_shared_deleted(self.share_token)
        invalidations.append({
            "transport": "PRODUCT_HTTP", "caller_role": "public_share",
            "credential_kind": "share_token", "fixture_effect": DELETE_FILE_EFFECT,
            **shared_content_absence,
        })
        evidence = dict(result.evidence)
        evidence["fixture_effect"] = DELETE_FILE_EFFECT
        evidence["invalidations"] = invalidations
        return AdapterCall(data={"file_deleted": True, "invalidation_count": 3},
                           evidence=evidence)


def _validate_step(requirement_id: str, adapter: object,
                   result: AdapterCall) -> None:
    if not isinstance(adapter, RemoteSessionFileCoreAdapter):
        raise RunStateError("Session-file core adapter is invalid")
    credential_kind = ("owned_local_bot_token"
                       if adapter.profile["environment"]["tier"] == "local"
                       else "human_cookie_with_owned_driver_bot_token")
    if requirement_id == "CLI-FILE.setup":
        evidence = result.evidence
        requests = evidence.get("requests") if isinstance(evidence, dict) else None
        if (not isinstance(evidence, dict)
                or evidence.get("transport") != "PRODUCT_CLI"
                or evidence.get("fixture_effect") != FIXTURE_EFFECT
                or evidence.get("remote_validation") != "NOT_RUN"
                or evidence.get("grants_release_credit") is not False
                or not isinstance(requests, list) or len(requests) != 2
                or requests[0].get("operation") != "session-file.group.create"
                or requests[1].get("operation") != "session-file.session.create"
                or requests[0].get("credential_kind") != credential_kind
                or requests[1].get("credential_kind") != credential_kind):
            raise RunStateError("Session-file setup evidence is not exact")
    else:
        operations = {
            "CLI-FILE.upload": "session-file.upload",
            "CLI-FILE.list": "session-file.list",
            "CLI-FILE.capabilities": "session-file.capabilities",
            "CLI-FILE.metadata-status": "file.metadata",
            "CLI-FILE.metadata-file-id": "file.metadata",
            "CLI-FILE.download": "session-file.download",
            "CLI-FILE.share": "session-file.share",
            "CLI-FILE.shared-metadata-status": "file.shared_metadata",
            "CLI-FILE.shared-metadata-hides-session": "file.shared_metadata",
            "CLI-FILE.shared-content-bytes": "file.shared_download",
            "CLI-FILE.delete": "session-file.delete",
        }
        if requirement_id == "CLI-FILE.metadata-status":
            evidence = _validated_request(
                result, operation=operations[requirement_id],
                credential_kind=credential_kind, transport="PRODUCT_HTTP")
            if evidence.get("http_status") != 200:
                raise RunStateError("Session-file metadata status is not 200")
        elif requirement_id == "CLI-FILE.metadata-file-id":
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind=credential_kind,
                               transport="PRODUCT_HTTP")
            if (not isinstance(result.data, dict)
                    or result.data.get("file_id") != adapter.file_id):
                raise RunStateError("Session-file metadata exact file id differs")
        elif requirement_id == "CLI-FILE.shared-metadata-status":
            evidence = _validated_request(
                result, operation=operations[requirement_id],
                credential_kind="share_token", transport="PRODUCT_HTTP",
                caller_role="public_share")
            if evidence.get("http_status") != 200:
                raise RunStateError("Session-file shared metadata status is not 200")
        elif requirement_id == "CLI-FILE.shared-metadata-hides-session":
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind="share_token",
                               transport="PRODUCT_HTTP", caller_role="public_share")
            if (not isinstance(result.data, dict)
                    or "session_id" in result.data):
                raise RunStateError("Session-file shared metadata exposes session_id")
        elif requirement_id == "CLI-FILE.shared-content-bytes":
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind="share_token",
                               transport="PRODUCT_HTTP", caller_role="public_share")
            expected_sha = hashlib.sha256(FILE_BYTES).hexdigest()
            if (not isinstance(result.data, dict)
                    or result.data.get("size") != FILE_SIZE
                    or result.data.get("sha256") != expected_sha):
                raise RunStateError("Session-file shared content bytes differ")
        elif requirement_id == "CLI-FILE.delete":
            evidence = _validated_request(result, operation=operations[requirement_id],
                                          credential_kind=credential_kind,
                                          fixture_effect=DELETE_FILE_EFFECT)
            invalidations = evidence.get("invalidations")
            if not isinstance(invalidations, list) or len(invalidations) != 3:
                raise RunStateError("Session-file deletion invalidations are not exact")
            expected = (
                ("file.verify_deleted", "driver", credential_kind),
                ("file.shared_metadata_verify_deleted", "public_share", "share_token"),
                ("file.shared_verify_deleted", "public_share", "share_token"),
            )
            for record, (operation, caller_role, cred_kind) in zip(invalidations, expected):
                if (not isinstance(record, dict)
                        or record.get("transport") != "PRODUCT_HTTP"
                        or record.get("operation") != operation
                        or record.get("http_status") != 404
                        or record.get("caller_role") != caller_role
                        or record.get("credential_kind") != cred_kind
                        or record.get("fixture_effect") != DELETE_FILE_EFFECT):
                    raise RunStateError("Session-file deletion invalidation is not exact")
        else:
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind=credential_kind)


def execute_session_file_core_case(adapter: object) -> CaseExecution:
    plan = session_file_core_case_plan()
    validate_session_file_core_adapter(adapter)
    if not isinstance(adapter, RemoteSessionFileCoreAdapter):
        raise RunStateError("Session-file core requires its shared adapter")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    results: dict[str, AdapterCall] = {}
    for requirement_id in ASSERTION_IDS:
        method_name = ASSERTION_METHODS[requirement_id]
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                     method=method_name,
                                     status="NOT_RUN", data=None, evidence={},
                                     error_type=None))
            continue
        try:
            result = results.get(method_name)
            if result is None:
                result = getattr(adapter, method_name)()
                if not isinstance(result, AdapterCall):
                    raise RunStateError("Session-file core method did not return AdapterCall")
                result.validate()
                results[method_name] = result
            _validate_step(requirement_id, adapter, result)
            steps.append(StepOutcome(requirement_id=requirement_id,
                                     method=method_name, status="PASS",
                                     data=result.data, evidence=dict(result.evidence),
                                     error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            detail = record_error_detail(error)
            if first_error_detail is None:
                first_error_detail = detail
            steps.append(StepOutcome(requirement_id=requirement_id,
                                     method=method_name,
                                     status="NOT_RUN", data=None, evidence={},
                                     error_type=error_type,
                                     error_detail=detail))
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=SESSION_FILE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed, total_count=len(steps),
        error_type=error_type, error_detail=first_error_detail,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Reviewed split: the product-CLI slices live in ``session_cli_contract`` and are
# re-exported here so the existing command registry, case guard, and tests keep
# importing them from one stable module.
from .session_cli_contract import (  # noqa: E402
    CASE_METHOD,
    CASE_CLASSES,
    FINAL_EFFECT,
    SESSION_CLI_BOT_GROUPS_CASE_ID,
    SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID,
    SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION,
    SESSION_CLI_CREATE_GET_CASE_ID,
    SESSION_CLI_CREATE_GET_REQUIREMENT_ID,
    SOURCE_FUNCTION,
    RemoteSessionCliBotGroupsAdapter,
    RemoteSessionCliCreateGetAdapter,
    SessionCliCreateGetCasePlan,
    execute_session_cli_bot_groups_case,
    execute_session_cli_create_get_case,
    session_cli_bot_groups_adaptability,
    session_cli_bot_groups_case_plan,
    session_cli_create_get_adaptability,
    session_cli_create_get_case_plan,
    SESSION_CLI_BOT_GROUPS_CASE_METHOD,
    validate_session_cli_create_get_adapter,
)
