"""Shared product-CLI cases for the dual-environment Session slice."""

from __future__ import annotations

import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_session_contract import session_source_binding
from .run_state import RunStateError
from .session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_CHAT_OPERATION,
    SESSION_CLI_SESSION_DELETE_OPERATION,
    SESSION_CLI_SESSION_GROUP_LISTING_OPERATION,
    SESSION_CLI_SESSION_INVITE_JOIN_OPERATION,
    SESSION_CLI_SESSION_MEMBER_ADD_OPERATION,
    SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION,
    SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION,
    SESSION_CLI_SESSION_MESSAGES_OPERATION,
    SESSION_CLI_SESSION_PATCH_TITLE_OPERATION,
    validate_session_cli_create_get_profile,
    validate_session_cli_lifecycle_profile,
)
from .workspace_cli_http_evidence import _invite_url_matches_product_shape
from .write_transport import WriteClient


SESSION_CLI_INVITE_JOIN_CASE_ID = "DUAL-SESSION-CLI-INVITE-JOIN-01"
SESSION_CLI_INVITE_JOIN_REQUIREMENT_ID = "SESSION.invite-join"
SESSION_CLI_INVITE_JOIN_SOURCE_FUNCTION = "test_session_invite_join"
SESSION_CLI_INVITE_JOIN_CASE_METHOD = "execute_session_cli_invite_join_case"
SESSION_CLI_INVITE_JOIN_EFFECT = "OWNED_GROUP_SESSION_INVITE_USED_THEN_GENERIC_GROUP_DELETE"


def session_cli_invite_join_case_plan() -> dict:
    binding = session_source_binding()
    row = next((
        item for item in binding["requirements"]
        if item["id"] == SESSION_CLI_INVITE_JOIN_REQUIREMENT_ID
        and item["function"] == SESSION_CLI_INVITE_JOIN_SOURCE_FUNCTION
    ), None)
    if row is None:
        raise RunStateError("Session CLI invite-join source obligation changed")
    return {
        "case_id": SESSION_CLI_INVITE_JOIN_CASE_ID,
        "requirement_id": SESSION_CLI_INVITE_JOIN_REQUIREMENT_ID,
        "source_function": SESSION_CLI_INVITE_JOIN_SOURCE_FUNCTION,
        "source_expectation": row["expectation"],
    }


def session_cli_invite_join_adaptability() -> dict:
    return {
        "case_id": SESSION_CLI_INVITE_JOIN_CASE_ID,
        "legacy_id": "story_user_runs_and_shares_sessions",
        "requirement_ids": [SESSION_CLI_INVITE_JOIN_REQUIREMENT_ID],
        "source_function": SESSION_CLI_INVITE_JOIN_SOURCE_FUNCTION,
        "selected_obligations": [
            "cli-group-create", "explicit-session-create", "session-invite-mint",
            "human-session-join", "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": "Mock-only; live Singlebox/preprod effect admission remains a separate gate.",
    }


class RemoteSessionCliInviteJoinAdapter:
    """Mint and consume one exact owned Session invitation, then clean up."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_cli_create_get_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Session CLI invite-join requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.session_id: str | None = None
        self.invite_token = ""
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
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

    def create_owned_cli_group(self) -> AdapterCall:
        result = self.runner.execute("session-cli.group.create")
        data = result.get("data") if isinstance(result, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("session_id"), str)):
            raise RunStateError("Session CLI invite-join Group setup is not exact")
        self.group_id = data["group_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "setup",
            "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def create_shared_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError("Session CLI invite-join must follow Group setup")
        result = self.runner.execute(
            "session-cli.session.create", group_id=self.group_id,
            session_title="share-sess",
        )
        data = result["data"]
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data.get("id") != data.get("session_id")
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "share-sess"):
            raise RunStateError("Session CLI invite-join explicit Session is not exact")
        self.session_id = data["session_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "session-create",
            "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def mint_session_invitation(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session CLI invite-join must follow explicit Session create")
        result = self.runner.execute(
            "session-cli.session.invite", session_id=self.session_id,
        )
        data = result["data"]
        if (not isinstance(data, dict)
                or set(data) != {"invite_token", "expires_at", "join_url"}
                or type(data["expires_at"]) is not int
                or not isinstance(data["invite_token"], str)
                or not data["invite_token"].isascii()
                or not 1 <= len(data["invite_token"]) <= 4096
                or any(ord(char) <= 32 or ord(char) == 127 for char in data["invite_token"])
                or not isinstance(data["join_url"], str)
                or len(data["join_url"]) > 8192):
            raise RunStateError("Session CLI invite-join invitation fields are invalid")
        if not _invite_url_matches_product_shape(
                data["join_url"], "sessions", data["invite_token"],
                self.profile["environment"]["tier"]):
            raise RunStateError("Session CLI invite-join URL differs from the product contract")
        self.invite_token = data["invite_token"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "session-invite",
            "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
        })
        return AdapterCall(
            data={
                "expires_at": data["expires_at"],
                "invite_token_present": True,
                "invite_url_product_shape_matches": True,
            },
            evidence=evidence,
        )

    def join_session_with_invitation(self) -> AdapterCall:
        if self.session_id is None or not self.invite_token:
            raise RunStateError("Session CLI invite-join must follow its exact invitation")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_INVITE_JOIN_OPERATION,
            session_id=self.session_id, invite_token=self.invite_token,
            body={}, expected=(200,),
        )
        expected = {
            "joined": True,
            "already_member": False,
            "target_type": "session",
            "target_id": self.session_id,
            "actor_id": "human_" + self.profile["human"]["user_id"],
        }
        if data != expected or record.get("http_status") != 200:
            raise RunStateError("Session CLI invite-join relation is not exact")
        self.invite_token = ""
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-join",
            "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=expected, evidence=evidence)

    def delete_owned_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI invite-join cleanup requires the exact Group ID")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_DELETE_OPERATION,
            group_id=self.group_id, expected=(200,),
        )
        if (data != {"deleted": True, "id": self.group_id}
                or record.get("http_status") != 200):
            raise RunStateError("Session CLI invite-join Group delete acknowledgement is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "cleanup-delete",
            "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def verify_group_deleted(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI invite-join cleanup requires the exact Group ID")
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
            raise RunStateError("Session CLI invite-join deleted Group was not an exact 404")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "cleanup-404-proof",
            "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)


def execute_session_cli_invite_join_case(adapter: object) -> CaseExecution:
    session_cli_invite_join_case_plan()
    if not isinstance(adapter, RemoteSessionCliInviteJoinAdapter):
        raise RunStateError("Session CLI invite-join requires its shared adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_shared_cli_session(),
            adapter.mint_session_invitation(),
            adapter.join_session_with_invitation(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            "session-cli.session.invite",
            SESSION_CLI_SESSION_INVITE_JOIN_OPERATION,
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        credential_kind = adapter._credential_kind()
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(
                    f"Session CLI invite-join {operation} did not return AdapterCall"
                )
            call.validate()
            if (call.evidence.get("credential_kind") != credential_kind
                    or call.evidence.get("fixture_effect") != SESSION_CLI_INVITE_JOIN_EFFECT
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False):
                raise RunStateError(
                    f"Session CLI invite-join evidence for {operation} is invalid"
                )
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_CLI_INVITE_JOIN_REQUIREMENT_ID,
        method=SESSION_CLI_INVITE_JOIN_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "session_invite_minted": True,
            "joined": True,
            "already_member": False,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": SESSION_CLI_INVITE_JOIN_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=SESSION_CLI_INVITE_JOIN_CASE_ID,
        parent_id="story_user_runs_and_shares_sessions",
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=1 if error_type is None else 0, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Dual product-CLI Session lifecycle case
SESSION_CLI_LIFECYCLE_CASE_ID = "DUAL-SESSION-CLI-LIFECYCLE-01"
SESSION_CLI_LIFECYCLE_REQUIREMENT_ID = "SESSION.lifecycle"
SESSION_CLI_LIFECYCLE_SOURCE_FUNCTION = "test_session_lifecycle"
SESSION_CLI_LIFECYCLE_CASE_METHOD = "execute_session_cli_lifecycle_case"
SESSION_CLI_LIFECYCLE_EFFECT = "OWNED_SESSION_LIFECYCLE_THEN_GENERIC_GROUP_DELETE"


def session_cli_lifecycle_case_plan() -> dict:
    binding = session_source_binding()
    row = next((
        item for item in binding["requirements"]
        if item["id"] == SESSION_CLI_LIFECYCLE_REQUIREMENT_ID
        and item["function"] == SESSION_CLI_LIFECYCLE_SOURCE_FUNCTION
    ), None)
    if row is None:
        raise RunStateError("Session CLI lifecycle source obligation changed")
    return {
        "case_id": SESSION_CLI_LIFECYCLE_CASE_ID,
        "requirement_id": SESSION_CLI_LIFECYCLE_REQUIREMENT_ID,
        "source_function": SESSION_CLI_LIFECYCLE_SOURCE_FUNCTION,
        "source_expectation": row["expectation"],
    }


def session_cli_lifecycle_adaptability() -> dict:
    return {
        "case_id": SESSION_CLI_LIFECYCLE_CASE_ID,
        "legacy_id": "story_user_runs_and_shares_sessions",
        "requirement_ids": [SESSION_CLI_LIFECYCLE_REQUIREMENT_ID],
        "source_function": SESSION_CLI_LIFECYCLE_SOURCE_FUNCTION,
        "selected_obligations": [
            "cli-group-create", "explicit-session-create", "session-rename",
            "session-member-add", "session-member-mute", "session-chat",
            "session-history-readback", "session-member-remove",
            "session-creator-delete", "session-absence-verification",
            "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": "Mock-only; live Singlebox/preprod effect admission remains a separate gate.",
    }


class RemoteSessionCliLifecycleAdapter:
    """Run the full owned Session lifecycle, then clean up the Group."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_cli_lifecycle_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Session CLI lifecycle requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.session_id: str | None = None
        self.deleted_session: str | None = None
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
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

    def create_owned_cli_group(self) -> AdapterCall:
        result = self.runner.execute("session-cli.group.create")
        data = result.get("data") if isinstance(result, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("session_id"), str)):
            raise RunStateError("Session CLI lifecycle Group setup is not exact")
        self.group_id = data["group_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "setup",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def create_lifecycle_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError("Session CLI lifecycle must follow Group setup")
        result = self.runner.execute(
            "session-cli.session.create", group_id=self.group_id,
            session_title="lifecycle-sess",
        )
        data = result["data"]
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data.get("id") != data.get("session_id")
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "lifecycle-sess"):
            raise RunStateError("Session CLI lifecycle explicit Session is not exact")
        self.session_id = data["session_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "session-create",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def _require_session_state(self, data: object, *, session_title: str | None = None,
                               member_present: bool | None = None,
                               member_mode: str | None = None) -> dict:
        if not isinstance(data, dict):
            raise RunStateError("Session CLI lifecycle Session state is not an object")
        if (data.get("id") != self.session_id
                or ("session_id" in data
                    and data.get("session_id") != self.session_id)
                or data.get("group_id") != self.group_id):
            raise RunStateError("Session CLI lifecycle Session state identity is not exact")
        if session_title is not None and data.get("session_title") != session_title:
            raise RunStateError("Session CLI lifecycle Session title is not exact")
        participants = data.get("participants")
        if not isinstance(participants, list) or not all(
                isinstance(participant, dict) for participant in participants):
            raise RunStateError("Session CLI lifecycle participants are invalid")
        states = {
            participant["bot_uuid"]: participant
            for participant in participants
            if isinstance(participant.get("bot_uuid"), str)
        }
        if member_present is True and self.member_id not in states:
            raise RunStateError("Session CLI lifecycle member admission is missing")
        if member_present is False and self.member_id in states:
            raise RunStateError("Session CLI lifecycle member removal is missing")
        if member_mode is not None:
            member = states.get(self.member_id)
            if member is None or member.get("mode") != member_mode:
                raise RunStateError("Session CLI lifecycle member mode is not exact")
        return data

    def rename_session_title(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI lifecycle rename requires its exact Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_PATCH_TITLE_OPERATION,
            session_id=self.session_id,
            body={"session_title": "renamed-sess"}, expected=(200,),
        )
        self._require_session_state(data, session_title="renamed-sess")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-rename",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(
            data={"session_id": self.session_id, "session_title": "renamed-sess"},
            evidence=evidence,
        )

    def add_session_member(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI lifecycle member add requires its exact Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_MEMBER_ADD_OPERATION,
            session_id=self.session_id,
            body={"bot_uuid": self.member_id}, expected=(200,),
        )
        self._require_session_state(data, member_present=True)
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-member-add",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(
            data={"session_id": self.session_id, "member_admitted": True},
            evidence=evidence,
        )

    def mute_session_member(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI lifecycle member mute requires its exact Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION,
            session_id=self.session_id,
            body={"mode": "muted"}, expected=(200,),
        )
        self._require_session_state(data, member_mode="muted")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-member-mute",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(
            data={"session_id": self.session_id, "member_mode": "muted"},
            evidence=evidence,
        )

    def send_session_chat(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI lifecycle chat requires its exact Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_CHAT_OPERATION,
            session_id=self.session_id,
            body={"message": "hello-session"}, expected=(200,),
        )
        if (not isinstance(data, dict)
                or type(data.get("delivered")) is not bool
                or data.get("session_id") != self.session_id
                or data.get("group_id") != self.group_id):
            raise RunStateError("Session CLI lifecycle chat acknowledgement is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-chat",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(
            data={
                "session_id": self.session_id,
                "delivered": data["delivered"],
            },
            evidence=evidence,
        )

    def read_session_history(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI lifecycle history requires its exact Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_MESSAGES_OPERATION,
            session_id=self.session_id, expected=(200,),
        )
        items: list[object]
        if isinstance(data, dict):
            items = data.get("items")
        elif isinstance(data, list):
            items = data
        else:
            items = None
        if (not isinstance(items, list)
                or not all(isinstance(item, dict) for item in items)):
            raise RunStateError("Session CLI lifecycle history readback is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-history",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(
            data={"session_id": self.session_id, "history_entries": len(items)},
            evidence=evidence,
        )

    def remove_session_member(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI lifecycle member remove requires its exact Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION,
            session_id=self.session_id, expected=(200,),
        )
        self._require_session_state(data, member_present=False)
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-member-remove",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(
            data={"session_id": self.session_id, "member_removed": True},
            evidence=evidence,
        )

    def delete_owned_session(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI lifecycle delete requires its exact Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_DELETE_OPERATION,
            session_id=self.session_id, expected=(200,),
        )
        if (data != {"deleted": True, "session_id": self.session_id}
                or record.get("http_status") != 200):
            raise RunStateError("Session CLI lifecycle delete acknowledgement is not exact")
        self.deleted_session = self.session_id
        self.session_id = ""
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-delete",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def verify_session_absent(self) -> AdapterCall:
        if self.group_id is None or self.deleted_session is None:
            raise RunStateError("Session CLI lifecycle absence requires its deleted Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_GROUP_LISTING_OPERATION,
            group_id=self.group_id, expected=(200,),
        )
        items: list[object]
        if isinstance(data, dict):
            items = []
            for key in ("items", "sessions"):
                value = data.get(key)
                if isinstance(value, list):
                    items = value
                    break
        elif isinstance(data, list):
            items = data
        else:
            raise RunStateError("Session CLI lifecycle listing readback is not exact")
        listed: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                raise RunStateError("Session CLI lifecycle listing entries are invalid")
            for key in ("session_id", "id"):
                value = item.get(key)
                if isinstance(value, str):
                    listed.append(value)
                    break
        if self.deleted_session in listed:
            raise RunStateError("Session CLI lifecycle deleted Session is still listed")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "session-absence-verify",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(
            data={"group_id": self.group_id, "session_absent": True},
            evidence=evidence,
        )

    def delete_owned_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI lifecycle cleanup requires the exact Group ID")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_DELETE_OPERATION,
            group_id=self.group_id, expected=(200,),
        )
        if (data != {"deleted": True, "id": self.group_id}
                or record.get("http_status") != 200):
            raise RunStateError("Session CLI lifecycle Group delete acknowledgement is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "cleanup-delete",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def verify_group_deleted(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI lifecycle cleanup requires the exact Group ID")
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
            raise RunStateError("Session CLI lifecycle deleted Group was not an exact 404")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": "cleanup-404-proof",
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)


def execute_session_cli_lifecycle_case(adapter: object) -> CaseExecution:
    session_cli_lifecycle_case_plan()
    if not isinstance(adapter, RemoteSessionCliLifecycleAdapter):
        raise RunStateError("Session CLI lifecycle requires its shared adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_lifecycle_cli_session(),
            adapter.rename_session_title(),
            adapter.add_session_member(),
            adapter.mute_session_member(),
            adapter.send_session_chat(),
            adapter.read_session_history(),
            adapter.remove_session_member(),
            adapter.delete_owned_session(),
            adapter.verify_session_absent(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            SESSION_CLI_SESSION_PATCH_TITLE_OPERATION,
            SESSION_CLI_SESSION_MEMBER_ADD_OPERATION,
            SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION,
            SESSION_CLI_SESSION_CHAT_OPERATION,
            SESSION_CLI_SESSION_MESSAGES_OPERATION,
            SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION,
            SESSION_CLI_SESSION_DELETE_OPERATION,
            SESSION_CLI_SESSION_GROUP_LISTING_OPERATION,
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        credential_kind = adapter._credential_kind()
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(
                    f"Session CLI lifecycle {operation} did not return AdapterCall"
                )
            call.validate()
            if (call.evidence.get("credential_kind") != credential_kind
                    or call.evidence.get("fixture_effect") != SESSION_CLI_LIFECYCLE_EFFECT
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False):
                raise RunStateError(
                    f"Session CLI lifecycle evidence for {operation} is invalid"
                )
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
        session_id = ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_CLI_LIFECYCLE_REQUIREMENT_ID,
        method=SESSION_CLI_LIFECYCLE_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_created": True,
            "session_renamed": True,
            "member_admitted": True,
            "member_muted": True,
            "session_chat_exercised": True,
            "history_readback": True,
            "member_removed": True,
            "session_deleted": True,
            "session_absence_verified": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": SESSION_CLI_LIFECYCLE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=SESSION_CLI_LIFECYCLE_CASE_ID,
        parent_id="story_user_runs_and_shares_sessions",
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=1 if error_type is None else 0, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
