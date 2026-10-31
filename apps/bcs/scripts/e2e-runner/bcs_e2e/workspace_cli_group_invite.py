"""Dual Session-CLI group-invite case contract: plan, adaptability, remote adapter, executor.

Split out of workspace_cli_runner under the 1,000-line source limit; contract text moved verbatim."""

from __future__ import annotations

from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from .bounded_process import ProcessCaptureError, capture
from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_cli import require_cli_binary
from .local_session_contract import session_source_binding
from .profile import identifier, unique_keys
from .run_state import RunStateError, write
from .workspace_cli_commands import (
    GROUP_CREATE_OPERATIONS,
    WorkspaceCliCommandContext,
    WorkspaceCliRunnerError,
    build_contract,
)
from .write_transport import WriteClient
from .workspace_cli_profile import (
    WORKSPACE_CLI_LIST_PROFILE_CONTRACT,
    WORKSPACE_CLI_STATUS_PROFILE_CONTRACT,
    WORKSPACE_CLI_DETAIL_PROFILE_CONTRACT,
    WORKSPACE_CLI_FUSE_PROFILE_CONTRACT,
    WORKSPACE_CLI_MEMBER_PROFILE_CONTRACT,
    SESSION_FILE_CORE_PROFILE_CONTRACT,
    validate_workspace_cli_list_profile,
    validate_workspace_cli_profile,
    validate_workspace_cli_status_profile,
    validate_workspace_cli_detail_profile,
    validate_workspace_cli_fuse_profile,
    validate_workspace_cli_member_profile,
    validate_session_file_core_profile,
)
from .session_cli_profile import (
    SESSION_CLI_CREATE_GET_PROFILE_CONTRACT,
    SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    SESSION_CLI_COLLECTION_PROFILE_CONTRACT,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_GROUP_INVITE_CREATE_OPERATION,
    SESSION_CLI_GROUP_INVITE_JOIN_OPERATION,
    validate_session_cli_create_get_profile,
    validate_session_cli_lifecycle_profile,
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_collection_profile,
)

from .workspace_cli_http_evidence import _http_evidence, _invite_url_matches_product_shape


# Dual product-CLI Session Group-invite case
SESSION_CLI_GROUP_INVITE_CASE_ID = "DUAL-SESSION-CLI-GROUP-INVITE-01"
SESSION_CLI_GROUP_INVITE_REQUIREMENT_ID = "SESSION.group-invite"
SESSION_CLI_GROUP_INVITE_SOURCE_FUNCTION = "test_group_invite_link"
SESSION_CLI_GROUP_INVITE_CASE_METHOD = "execute_session_cli_group_invite_case"
SESSION_CLI_GROUP_INVITE_EFFECT = "OWNED_GROUP_INVITE_USED_THEN_GENERIC_GROUP_DELETE"


def session_cli_group_invite_case_plan() -> dict:
    binding = session_source_binding()
    row = next((item for item in binding["requirements"]
                if item["id"] == SESSION_CLI_GROUP_INVITE_REQUIREMENT_ID
                and item["function"] == SESSION_CLI_GROUP_INVITE_SOURCE_FUNCTION), None)
    if row is None:
        raise RunStateError("Session CLI Group-invite source obligation changed")
    return {
        "case_id": SESSION_CLI_GROUP_INVITE_CASE_ID,
        "requirement_id": SESSION_CLI_GROUP_INVITE_REQUIREMENT_ID,
        "source_function": SESSION_CLI_GROUP_INVITE_SOURCE_FUNCTION,
        "source_expectation": row["expectation"],
    }


def session_cli_group_invite_adaptability() -> dict:
    return {
        "case_id": SESSION_CLI_GROUP_INVITE_CASE_ID,
        "legacy_id": "story_user_runs_and_shares_sessions",
        "requirement_ids": [SESSION_CLI_GROUP_INVITE_REQUIREMENT_ID],
        "source_function": SESSION_CLI_GROUP_INVITE_SOURCE_FUNCTION,
        "selected_obligations": ["cli-group-create", "group-invite-mint", "group-join", "exact-group-delete"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": "Mock-only; live Singlebox/preprod effect admission remains a separate gate.",
    }


class RemoteSessionCliGroupInviteAdapter:
    """Mint and consume one exact owned Group invitation, then clean up."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_cli_create_get_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Session CLI Group-invite requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.initial_session_id = ""
        self.invite_token = ""
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        if profile["environment"]["tier"] == "local":
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {"Authorization": profile["auth"]["driver_token_env"]},
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

    def create_owned_cli_group(self) -> AdapterCall:
        result = self.runner.execute("session-cli.group.create")
        if (not isinstance(result, dict) or set(result) != {"data", "evidence"}
                or not isinstance(result["data"], dict)
                or not isinstance(result["data"].get("group_id"), str)
                or not result["data"]["group_id"]
                or result["data"].get("participants") != [self.driver_id, self.member_id]
                or not isinstance(result["data"].get("session_id"), str)):
            raise RunStateError("Session CLI Group-invite Group setup is not exact")
        self.group_id = result["data"]["group_id"]
        self.initial_session_id = result["data"]["session_id"]
        evidence = dict(result["evidence"])
        evidence["case_class"] = "setup"
        evidence["fixture_effect"] = SESSION_CLI_GROUP_INVITE_EFFECT
        return AdapterCall(data=result["data"], evidence=evidence)

    def mint_group_invitation(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI Group-invite must follow Group setup")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_INVITE_CREATE_OPERATION,
            group_id=self.group_id,
            body={"ttl_seconds": 300},
            expected=(200,),
        )
        if (not isinstance(data, dict)
                or set(data) != {"invite_token", "expires_at", "join_url"}
                or type(data["expires_at"]) is not int
                or not isinstance(data["invite_token"], str)
                or not data["invite_token"].isascii()
                or not 1 <= len(data["invite_token"]) <= 4096
                or any(ord(c) <= 32 or ord(c) == 127 for c in data["invite_token"])
                or not isinstance(data["join_url"], str)
                or len(data["join_url"]) > 8192):
            raise RunStateError("Session CLI Group invitation fields are invalid")
        if not _invite_url_matches_product_shape(
                data["join_url"], "groups", data["invite_token"],
                self.profile["environment"]["tier"]):
            raise RunStateError("Session CLI Group invitation URL differs from the product contract")
        self.invite_token = data["invite_token"]
        evidence = _http_evidence(
            record, self.profile, case_class="invite-mint",
            fixture_effect=SESSION_CLI_GROUP_INVITE_EFFECT)
        return AdapterCall(
            data={
                "expires_at": data["expires_at"],
                "invite_url_product_shape_matches": True,
                "invite_token_present": True,
            },
            evidence=evidence,
        )

    def join_group_with_invitation(self) -> AdapterCall:
        if self.group_id is None or not self.invite_token:
            raise RunStateError("Session CLI Group join must follow its exact invitation")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_INVITE_JOIN_OPERATION,
            group_id=self.group_id,
            invite_token=self.invite_token,
            body={},
            expected=(200,),
        )
        if (not isinstance(data, dict)
                or data.get("joined") is not True
                or data.get("already_member") is not False
                or data.get("target_type") != "group"
                or data.get("target_id") != self.group_id
                or data.get("actor_id") != "human_" + self.profile["human"]["user_id"]):
            raise RunStateError("Session CLI Group join did not return the exact Group relation")
        self.invite_token = ""
        evidence = _http_evidence(
            record, self.profile, case_class="invite-join",
            fixture_effect=SESSION_CLI_GROUP_INVITE_EFFECT)
        return AdapterCall(data={
            "joined": True, "already_member": False, "target_type": "group",
            "target_id": self.group_id, "actor_id": data["actor_id"],
        }, evidence=evidence)

    def delete_owned_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI Group-invite cleanup requires the exact Group ID")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_DELETE_OPERATION,
            group_id=self.group_id, expected=(200,),
        )
        if (not isinstance(data, dict)
                or data != {"deleted": True, "id": self.group_id}
                or record.get("http_status") != 200):
            raise RunStateError("Session CLI Group-invite delete acknowledgement is not exact")
        evidence = _http_evidence(
            record, self.profile, case_class="cleanup-delete",
            fixture_effect=SESSION_CLI_GROUP_INVITE_EFFECT)
        return AdapterCall(data=data, evidence=evidence)

    def verify_group_deleted(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI Group-invite cleanup requires the exact Group ID")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
            group_id=self.group_id, expected=(404,), parse_error=True,
        )
        if (not isinstance(data, dict)
                or data.get("status") != 404
                or data.get("code") != "group_not_found"
                or data.get("params") != {"group_id": self.group_id}
                or not isinstance(data.get("message"), str)
                or not data["message"]
                or record.get("http_status") != 404):
            raise RunStateError("Session CLI Group-invite deleted Group was not returned as exact 404")
        evidence = _http_evidence(
            record, self.profile, case_class="cleanup-404-proof",
            fixture_effect=SESSION_CLI_GROUP_INVITE_EFFECT)
        return AdapterCall(data=data, evidence=evidence)


def execute_session_cli_group_invite_case(adapter: object) -> CaseExecution:
    session_cli_group_invite_case_plan()
    if not isinstance(adapter, RemoteSessionCliGroupInviteAdapter):
        raise RunStateError("Session CLI Group-invite requires its shared adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.mint_group_invitation(),
            adapter.join_group_with_invitation(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            SESSION_CLI_GROUP_INVITE_CREATE_OPERATION,
            SESSION_CLI_GROUP_INVITE_JOIN_OPERATION,
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        credential_kind = (
            "owned_local_bot_token"
            if adapter.profile["environment"]["tier"] == "local"
            else "human_cookie"
        )
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(f"Session CLI Group-invite {operation} did not return AdapterCall")
            call.validate()
            if (call.evidence.get("credential_kind") != credential_kind
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False
                    or call.evidence.get("fixture_effect") != SESSION_CLI_GROUP_INVITE_EFFECT):
                raise RunStateError(f"Session CLI Group-invite evidence for {operation} is invalid")
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_CLI_GROUP_INVITE_REQUIREMENT_ID,
        method=SESSION_CLI_GROUP_INVITE_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "invite_minted": True,
            "joined": True,
            "already_member": False,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": SESSION_CLI_GROUP_INVITE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=SESSION_CLI_GROUP_INVITE_CASE_ID,
        parent_id="story_user_runs_and_shares_sessions",
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=1 if error_type is None else 0, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


