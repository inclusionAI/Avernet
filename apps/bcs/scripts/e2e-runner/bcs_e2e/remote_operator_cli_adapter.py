"""Dual-environment Operator product-CLI remote adapter.

The adapter drives the thirteen reviewed obligations of
``story_operator_coordinates_with_cli`` through the bounded
``OperatorCliRunner`` product-CLI executor and, on the preprod station
legacy face, the two reviewed register edge bindings for the temporary
agent lifecycle. Every step returns an ``AdapterCall`` whose evidence
carries ``remote_validation=NOT_RUN``; the case receipt never grants
release credit, and the whole slice is mock-only until a live preprod
run produces its real receipt.

The temporary agent disposition stays contract-locked: the shared write
transport exposes the reviewed station Bot delete/read pair on the station
legacy retire vocabulary, the direct-onboard obligation admits the register
leg only against that reviewed pair, and the lifecycle closes with an exact
left acknowledgment plus a 404 read-back. Live Singlebox/preprod effect
admission and the real station register endpoint remain required before
any release credit.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .local_operator_cli_contract import (
    COMMANDS,
    PARENT,
    REQUIREMENTS,
    operator_cli_source_binding,
)
from .operator_cli_runner import (
    OPERATOR_CLI_CHAT,
    OPERATOR_CLI_CLI_OPERATIONS,
    OPERATOR_CLI_CONNECT,
    OPERATOR_CLI_CREATE_GROUP,
    OPERATOR_CLI_DISCOVER,
    OPERATOR_CLI_EFFECT,
    OPERATOR_CLI_FRIEND_ACCEPT_CHAT,
    OPERATOR_CLI_FRIEND_ACCEPT_LIST,
    OPERATOR_CLI_FRIEND_LIST,
    OPERATOR_CLI_FRIEND_REQUEST_CHAT,
    OPERATOR_CLI_FRIEND_REQUEST_LIST,
    OPERATOR_CLI_GET,
    OPERATOR_CLI_GET_GROUP,
    OPERATOR_CLI_HEALTH,
    OPERATOR_CLI_LIST,
    OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD,
    OPERATOR_CLI_LIST_GROUPS,
    OPERATOR_CLI_ONBOARD_DIRECT,
    OPERATOR_CLI_ONBOARD_READBACK,
    OPERATOR_CLI_ONBOARD_WEB,
    OPERATOR_CLI_UPDATE_STATUS,
    OPERATOR_CLI_VISIBILITY,
    validate_operator_cli_profile,
)
from .profile import ConfigError
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient, WriteTransportError

OPERATOR_CLI_CASE_ID = "DUAL-OPERATOR-CLI-01"

OPERATOR_CLI_CREDENTIAL_KINDS = (
    "none",
    "owned_local_bot_token",
    "human_cookie",
    "human_cookie_with_owned_driver_bot_token",
    "human_cookie_with_owned_onboarded_bot_token",
)

OPERATOR_CLI_ADAPTER_METHODS = (
    "operator_health",
    "operator_list",
    "operator_get",
    "operator_discover",
    "operator_visibility_round_trip",
    "operator_connect",
    "operator_onboard_web",
    "operator_direct_onboard",
    "operator_update_status",
    "operator_create_group",
    "operator_chat_with_pm",
    "operator_list_groups",
    "operator_friend_list",
)

OPERATOR_CLI_CLI_OBLIGATIONS = (
    "health", "list", "get", "discover", "visibility", "connect",
    "onboard-web", "direct-onboard", "update-status", "create-group",
    "chat", "list-groups", "friend-list",
)

OPERATOR_CLI_BLOCKER = (
    "Mock-only; the station register-token read, the anonymous register leg "
    "and the temporary agent disposition are proven through the reviewed "
    "write-transport pair (WriteClient.station_bot_delete and "
    "WriteClient.station_bot_read) with an exact left acknowledgment and a "
    "404 read-back, and the non-mutating web onboard is proven through the "
    "product-CLI list projection only. Live Singlebox/preprod effect "
    "admission and the real station register endpoint are required before "
    "any release credit."
)


def operator_cli_case_plan() -> dict:
    """Derive the frozen Operator CLI plan from its reviewed source."""
    binding = operator_cli_source_binding()
    requirement_ids = [row["id"] for row in binding["requirements"]]
    if (binding["parent"] != PARENT
            or requirement_ids != list(REQUIREMENTS)
            or [row["command"] for row in binding["requirements"]] != list(COMMANDS)
            or list(OPERATOR_CLI_CLI_OPERATIONS) != [
                "operator_cli.health", "operator_cli.list", "operator_cli.get",
                "operator_cli.discover", "operator_cli.visibility",
                "operator_cli.connect", "operator_cli.onboard_web",
                "operator_cli.list_after_web_onboard", "operator_cli.onboard_direct",
                "operator_cli.onboard_readback", "operator_cli.update_status",
                "operator_cli.create_group", "operator_cli.get_group",
                "operator_cli.friend_request_chat", "operator_cli.friend_accept_chat",
                "operator_cli.chat", "operator_cli.list_groups",
                "operator_cli.friend_request_list", "operator_cli.friend_accept_list",
                "operator_cli.friend_list",
            ]
            or OPERATOR_CLI_ADAPTER_METHODS != tuple(OPERATOR_CLI_ADAPTER_METHODS)
            or len(set(OPERATOR_CLI_ADAPTER_METHODS)) != len(OPERATOR_CLI_ADAPTER_METHODS)):
        raise RunStateError("operator CLI source obligation map changed")
    return {
        "case_id": OPERATOR_CLI_CASE_ID,
        "parent_id": PARENT,
        "requirement_ids": requirement_ids,
        "source_commands": [row["command"] for row in binding["requirements"]],
        "source_expectations": [row["expectation"] for row in binding["requirements"]],
        "adapter_methods": list(OPERATOR_CLI_ADAPTER_METHODS),
        "frozen_operations": list(OPERATOR_CLI_CLI_OPERATIONS),
    }


def operator_cli_adaptability() -> dict:
    plan = operator_cli_case_plan()
    return {
        "case_id": OPERATOR_CLI_CASE_ID,
        "legacy_id": plan["parent_id"],
        "requirement_ids": plan["requirement_ids"],
        "selected_obligations": list(OPERATOR_CLI_CLI_OBLIGATIONS),
        "cli_operations": plan["frozen_operations"],
        "cleanup_operations": [
            "operator_cli.group.delete", "operator_cli.group.deleted_verify",
            "station_bot_delete", "station_bot_read",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-operator",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": OPERATOR_CLI_BLOCKER,
    }


def validate_operator_cli_adapter(adapter: object) -> None:
    plan = operator_cli_case_plan()
    missing = [name for name in plan["adapter_methods"]
               if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError("operator CLI adapter is missing methods: "
                            + ", ".join(missing))


class RemoteOperatorCliAdapter:
    """Run the reviewed operator parent obligations, then clean up exactly."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_operator_cli_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("operator CLI requires a product-CLI runner")
        self.runner = runner
        self.tier = self.profile["environment"]["tier"]
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.eng_id = self.profile["actors"]["eng"]["bot_id"]
        auth = self.profile["auth"]
        self.cookie_env = auth.get("cookie_env", "")
        self.driver_token_env = auth.get("driver_token_env", "")
        self.transport: WriteClient | None = None
        if self.tier == "preprod":
            try:
                self.transport = WriteClient(self._station_profile())
            except (WriteTransportError, ConfigError) as error:
                raise RunStateError(
                    "operator CLI remote transport requires the reviewed "
                    "duo credentials") from error
        self.secrets: list[str] = [
            os.environ.get(self.cookie_env, "") if self.cookie_env else "",
            os.environ.get(self.driver_token_env, "") if self.driver_token_env else "",
        ]

    # -- Profile derivation -------------------------------------------------
    def _station_profile(self) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        value["auth"] = {
            "kind": "human-cookie-with-owned-driver-bot-token",
            "header_env": {"Cookie": self.cookie_env,
                           "Authorization": self.driver_token_env},
        }
        value["entrypoint"] = {
            "kind": self.profile["entrypoint"]["kind"],
            "origin": self.profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return value

    def _register_reader_profile(self) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        value["auth"] = {
            "kind": "human-cookie",
            "header_env": {"Cookie": self.cookie_env},
        }
        # The cookie-only station reader submits exactly the reviewed
        # register-token read contract. The owned-Bot disposal vocabulary
        # (station_bot_delete/station_bot_read) is dual-identity and its
        # fail-closed validation requires Cookie and Authorization
        # references; a cookie-only reader deliberately holds only the Cookie
        # reference, so the derived bindings must not advertise disposal
        # contracts this identity cannot submit.
        bindings = value.get("edge_case_bindings")
        if isinstance(bindings, dict):
            value["edge_case_bindings"] = {
                key: binding for key, binding in bindings.items()
                if key == "friend_connection_mutation.register_token.read"
            }
        return value

    def _credential_kind(self) -> str:
        return ("owned_local_bot_token"
                if self.tier == "local"
                else "human_cookie_with_owned_driver_bot_token")

    # -- Evidence projection ------------------------------------------------
    def _cli_step(self, results: list, case_class: str, data: dict) -> AdapterCall:
        evidence = dict(results[0]["evidence"])
        evidence["cli_operations"] = [row["evidence"]["operation"]
                                      for row in results]
        evidence["operator_cli_commands"] = [row["evidence"]["command_arguments"]
                                             for row in results]
        evidence["case_class"] = case_class
        evidence["fixture_effect"] = OPERATOR_CLI_EFFECT
        evidence["gaps"] = []
        evidence["defects"] = []
        return self._finish(AdapterCall(data=data, evidence=evidence))

    def _registry_read(self) -> tuple[dict, dict]:
        payload, record = self.transport.call(
            "operator_cli.bot_registry.read", expected=(200,))
        if record.get("http_status") != 200 or not isinstance(payload, dict):
            raise RunStateError("operator CLI registry read is not an exact bot record")
        return payload, record

    def _finish(self, call: AdapterCall) -> AdapterCall:
        blob = json.dumps({"data": call.data, "evidence": call.evidence},
                          sort_keys=True, default=str)
        for secret in self.secrets:
            if secret and secret in blob:
                raise RunStateError("operator CLI evidence leaked a case credential")
        return call

    # -- Thirteen reviewed obligations --------------------------------------
    def operator_health(self) -> AdapterCall:
        """Health exits zero without any Bot credential."""
        result = self.runner.execute(OPERATOR_CLI_HEALTH)
        if result["data"] != {"status": "healthy", "credential_free": True}:
            raise RunStateError("operator CLI health projection differs")
        return self._cli_step([result], "health", dict(result["data"]))

    def operator_list(self) -> AdapterCall:
        """List echoes all five trusted-team fixture Bot UUIDs."""
        result = self.runner.execute(OPERATOR_CLI_LIST)
        data = result["data"]
        if (not isinstance(data, dict)
                or data.get("fixture_count") != 5
                or len(data.get("fixture_uuids") or []) != 5):
            raise RunStateError("operator CLI fixture listing is not the complete five")
        return self._cli_step([result], "list", dict(data))

    def operator_get(self) -> AdapterCall:
        """Get echoes the requested driver fixture Bot UUID."""
        result = self.runner.execute(OPERATOR_CLI_GET, target=self.driver_id)
        data = result["data"]
        if not isinstance(data, dict) or data.get("target") != self.driver_id:
            raise RunStateError("operator CLI get did not echo the driver fixture Bot")
        return self._cli_step([result], "get", dict(data))

    def operator_discover(self) -> AdapterCall:
        """Discover returns the CS fixture Bot for the CS fixture-name query."""
        query = self.profile["fixture_names"]["cs"]
        result = self.runner.execute(OPERATOR_CLI_DISCOVER, query=query)
        data = result["data"]
        if (not isinstance(data, dict) or data.get("query") != query
                or data.get("bot_uuid") != self.profile["actors"]["cs"]["bot_id"]):
            raise RunStateError("operator CLI discover did not resolve the CS fixture Bot")
        return self._cli_step([result], "discover", dict(data))

    def operator_visibility_round_trip(self) -> AdapterCall:
        """Visibility get/set/restore round trip on the dedicated driver Bot."""
        result = self.runner.execute(OPERATOR_CLI_VISIBILITY)
        data = result["data"]
        if (not isinstance(data, dict)
                or data.get("original") not in {"public", "protected", "private"}
                or data.get("round_trip_verified") is not True):
            raise RunStateError("operator CLI visibility round trip is not exact")
        return self._cli_step([result], "visibility-round-trip", dict(data))

    def operator_connect(self) -> AdapterCall:
        """Connect returns the driver session handle; the token is never journaled."""
        result = self.runner.execute(OPERATOR_CLI_CONNECT)
        data = result["data"]
        if (not isinstance(data, dict) or data.get("bot_uuid") != self.driver_id
                or type(data.get("is_new")) is not bool
                or data.get("token_present") is not True):
            raise RunStateError("operator CLI connect handle projection differs")
        return self._cli_step([result], "connect", dict(data))

    def operator_onboard_web(self) -> AdapterCall:
        """Web onboard prints the registration URL and registers no Bot."""
        web_result = self.runner.execute(OPERATOR_CLI_ONBOARD_WEB)
        list_result = self.runner.execute(OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD)
        web_data = web_result["data"]
        list_data = list_result["data"]
        if (not isinstance(web_data, dict) or web_data.get("registered") is not False
                or not isinstance(list_data, dict)
                or list_data.get("probe_absent") is not True
                or list_data.get("fixture_uuids_present") is not True):
            raise RunStateError("operator CLI web onboard projection differs")
        data = {"probe_name": web_data.get("probe_name"), "registered": False,
                "probe_absent": True, "fixture_uuids_present": True}
        return self._cli_step([web_result, list_result], "web-onboard", data)

    def operator_direct_onboard(self) -> AdapterCall:
        """Register, onboard, read back, then delete the temporary agent exactly once.

        Singlebox keeps this obligation with the owned-local executor
        ``LOCAL-OPERATOR-CLI-01``. Preprod reads the register token through
        the cookie-only station reader, seeds the register token into the
        driver token reference for the anonymous register leg only, restores
        the driver token afterwards, onboards through the product CLI with
        the temporary Bot token, and closes the lifecycle with the exact
        station delete + 404 disposition. The disposition is contract-locked
        to the reviewed station Bot delete/read pair: the register leg runs
        only against that vocabulary and the temporary agent is retired
        exactly once with an exact left acknowledgment plus a 404 read-back.
        """
        if self.tier == "local":
            error = RunStateError(
                "operator CLI direct onboard stays with the owned-local executor "
                "LOCAL-OPERATOR-CLI-01 on Singlebox; the dual remote slice defers "
                "local registration to the owned runtime journal")
            error.gap = ("owned-local executor LOCAL-OPERATOR-CLI-01 owns the "
                         "Singlebox direct-onboard proof")
            raise error
        register_reader = ReadClient(self._register_reader_profile())
        observation = register_reader.read("station.register_token.read")
        register_token = observation.data.get("token")
        if (not isinstance(register_token, str) or not register_token
                or len(register_token) > 4096):
            raise RunStateError("station register token read returned no usable token")
        temp_name = "operator-cli-direct-" + uuid.uuid4().hex[:6]
        token_env = self.driver_token_env
        previous_present = token_env in os.environ
        previous = os.environ.get(token_env, "")
        os.environ[token_env] = register_token
        try:
            register_client = WriteClient(self._station_profile())
            registered, register_record = register_client.station_register_bot(
                register_token, temp_name)
        finally:
            if previous_present:
                os.environ[token_env] = previous
            else:
                os.environ.pop(token_env, None)
        bot_uuid = registered.get("bot_uuid")
        bot_token = registered.get("bot_token")
        if (registered.get("bot_name") != temp_name
                or not isinstance(bot_uuid, str) or not bot_uuid
                or not isinstance(bot_token, str) or not bot_token):
            raise RunStateError("station register response does not match the case projection")
        onboard_result = self.runner.execute(
            OPERATOR_CLI_ONBOARD_DIRECT, name=temp_name,
            onboard_token=bot_token, expected_bot_uuid=bot_uuid)
        readback_result = self.runner.execute(
            OPERATOR_CLI_ONBOARD_READBACK, bot_uuid=bot_uuid)
        deleted, delete_record = self.transport.station_bot_delete(bot_uuid)
        if (deleted.get("left") is not True
                or deleted.get("bot_uuid") != bot_uuid
                or delete_record.get("http_status") != 200):
            error = RunStateError(
                "station temporary Bot delete did not acknowledge exact disposal")
            error.gap = ("station temporary Bot delete did not acknowledge exact "
                         "disposal: the left acknowledgment, retiring bot_uuid, "
                         "and HTTP 200 must match exactly")
            raise error
        _, read_record = self.transport.station_bot_read(bot_uuid)
        if read_record.get("http_status") != 404:
            error = RunStateError(
                "deleted temporary Bot did not read back 404")
            error.gap = ("deleted temporary Bot did not read back 404: "
                         "the retired Bot tombstone must read back exactly")
            raise error
        self.secrets = [*self.secrets, register_token, bot_token]
        data = {"bot_id": bot_uuid, "bot_name": temp_name, "registered": True,
                "onboarded": True, "readback_verified": True,
                "disposition": "DELETED_404_VERIFIED",
                "onboarded_token_sha256": hashlib.sha256(bot_token.encode()).hexdigest(),
                "register_token_sha256": hashlib.sha256(register_token.encode()).hexdigest()}
        evidence = dict(onboard_result["evidence"])
        evidence["cli_operations"] = [onboard_result["evidence"]["operation"],
                                      readback_result["evidence"]["operation"]]
        evidence["operator_cli_commands"] = [
            onboard_result["evidence"]["command_arguments"],
            readback_result["evidence"]["command_arguments"]]
        evidence["case_class"] = "direct-onboard"
        evidence["transport"] = "PRODUCT_HTTP"
        evidence["http_case_caller_role"] = "case_owned_driver"
        evidence["fixture_effect"] = OPERATOR_CLI_EFFECT
        evidence["requests"] = [register_record, delete_record, read_record]
        evidence["register_token_read"] = observation.evidence
        evidence["driver_token_restored"] = True
        evidence["gaps"] = []
        evidence["defects"] = []
        return self._finish(AdapterCall(data=data, evidence=evidence))

    def operator_update_status(self) -> AdapterCall:
        """Status broadcast; the registry record is unchanged after both legs."""
        before, before_record = self._registry_read()
        result = self.runner.execute(OPERATOR_CLI_UPDATE_STATUS)
        after, after_record = self._registry_read()
        if before != after:
            raise RunStateError(
                "operator CLI update-status changed the registry record")
        data = {"before": before, "after": after, "registry_unchanged": True,
                "broadcast": "idle", "restore": "busy"}
        evidence = dict(result["evidence"])
        evidence["cli_operations"] = [result["evidence"]["operation"]]
        evidence["operator_cli_commands"] = [
            result["evidence"]["command_arguments"]]
        evidence["case_class"] = "update-status"
        evidence["transport"] = "PRODUCT_HTTP"
        evidence["http_case_caller_role"] = "case_owned_driver"
        evidence["fixture_effect"] = OPERATOR_CLI_EFFECT
        evidence["requests"] = [before_record, after_record]
        evidence["gaps"] = []
        evidence["defects"] = []
        return self._finish(AdapterCall(data=data, evidence=evidence))

    def operator_create_group(self) -> AdapterCall:
        """PM creates the exact PM/ENG Group; creator-scoped delete then 404."""
        create_result = self.runner.execute(OPERATOR_CLI_CREATE_GROUP)
        get_result = self.runner.execute(OPERATOR_CLI_GET_GROUP)
        create_data = create_result["data"]
        group_id = create_data.get("group_id")
        if (not isinstance(create_data, dict) or not isinstance(group_id, str)
                or not group_id
                or create_data.get("driver_bot") != self.member_id
                or create_data.get("participants") != [self.member_id, self.eng_id]
                or create_data.get("topic") != "operator cli e2e group"
                or get_result["data"].get("group_id") != group_id):
            raise RunStateError("operator CLI Group setup is not exact")
        deleted, delete_record = self.transport.call(
            "operator_cli.group.delete", group_id=group_id, expected=(200,))
        if deleted.get("deleted") is not True or delete_record.get("http_status") != 200:
            raise RunStateError(
                "creator-scoped Group delete did not acknowledge exact disposal")
        _, verify_record = self.transport.call(
            "operator_cli.group.deleted_verify", group_id=group_id, expected=(404,))
        if verify_record.get("http_status") != 404:
            raise RunStateError("deleted CLI-created Group did not read back 404")
        data = {"group_id": group_id, "driver_bot": self.member_id,
                "participants": [self.member_id, self.eng_id],
                "topic": "operator cli e2e group",
                "disposition": "CREATOR_SCOPED_DELETE_404_VERIFIED"}
        evidence = dict(create_result["evidence"])
        evidence["cli_operations"] = [create_result["evidence"]["operation"],
                                      get_result["evidence"]["operation"]]
        evidence["operator_cli_commands"] = [
            create_result["evidence"]["command_arguments"],
            get_result["evidence"]["command_arguments"]]
        evidence["case_class"] = "create-group"
        evidence["transport"] = "PRODUCT_HTTP"
        evidence["http_case_caller_role"] = "case_owned_member"
        evidence["fixture_effect"] = OPERATOR_CLI_EFFECT
        evidence["requests"] = [delete_record, verify_record]
        evidence["gaps"] = []
        evidence["defects"] = []
        return self._finish(AdapterCall(data=data, evidence=evidence))

    def operator_chat_with_pm(self) -> AdapterCall:
        """Admit the CEO<->PM pair in-case, then detach a chat handle.

        A stateful station that already retained this fixture pair answers
        the request leg idempotently; the runner substitutes the verified
        ALREADY_ADMITTED projection for a fabricated pending request ID, and
        this obligation records the accept substitution instead of invoking
        an accept route that has no pending request to decide.
        """
        request_result = self.runner.execute(OPERATOR_CLI_FRIEND_REQUEST_CHAT)
        request_state = request_result["data"]
        admission = request_state.get("admission", "ACCEPTED")
        request_id = request_state.get("request_id")
        if admission == "ALREADY_ADMITTED":
            if (request_id is not None
                    or request_state.get("friends_verified") is not True):
                raise RunStateError(
                    "operator CLI chat admission substitution is not verified")
        elif not isinstance(request_id, str) or not request_id:
            raise RunStateError("operator CLI chat admission did not return a request ID")
        accept_result = self.runner.execute(
            OPERATOR_CLI_FRIEND_ACCEPT_CHAT, request_id=request_id)
        accept_invoked = accept_result["data"].get("accept_invoked", True)
        chat_result = self.runner.execute(OPERATOR_CLI_CHAT)
        chat_data = chat_result["data"]
        if (not isinstance(chat_data, dict)
                or not isinstance(chat_data.get("run_id"), str)
                or not isinstance(chat_data.get("session_id"), str)):
            raise RunStateError("operator CLI chat handle projection differs")
        prerequisite = {"caller": self.driver_id, "target": self.member_id,
                        "request_id": request_id, "admission": admission,
                        "accept_invoked": accept_invoked}
        commands = [request_result["evidence"]["command_arguments"],
                    chat_result["evidence"]["command_arguments"]]
        if accept_invoked:
            commands.insert(1, ["friend", "accept", "--request-id", request_id])
        else:
            prerequisite["friends_verified"] = True
            prerequisite["substitution"] = (
                "station retained this case fixture admission; the accept "
                "route has no pending request to decide")
        data = {"prerequisite_admission": prerequisite,
                "run_id": chat_data["run_id"],
                "session_id": chat_data["session_id"],
                "detach_fallback": chat_data.get("detach_fallback") is True}
        evidence = dict(request_result["evidence"])
        evidence["cli_operations"] = [
            request_result["evidence"]["operation"],
            "operator_cli.friend_accept_chat", chat_result["evidence"]["operation"]]
        evidence["operator_cli_commands"] = commands
        evidence["case_class"] = "chat"
        evidence["fixture_effect"] = OPERATOR_CLI_EFFECT
        evidence["gaps"] = []
        evidence["defects"] = []
        return self._finish(AdapterCall(data=data, evidence=evidence))

    def operator_list_groups(self) -> AdapterCall:
        """List-groups returns the exact paginated page shape."""
        result = self.runner.execute(OPERATOR_CLI_LIST_GROUPS)
        data = result["data"]
        if (not isinstance(data, dict)
                or type(data.get("returned")) is not int
                or type(data.get("total")) is not int
                or type(data.get("has_more")) is not bool):
            raise RunStateError("operator CLI group page shape differs")
        return self._cli_step([result], "list-groups", dict(data))

    def operator_friend_list(self) -> AdapterCall:
        """Admit the CEO<->ENG pair in-case, then list friends containing ENG.

        The retained-station ALREADY_ADMITTED substitution mirrors the chat
        admission handling: an already-admitted fixture pair leaves no
        pending request, so the accept substitution is recorded, not
        invoked.
        """
        request_result = self.runner.execute(OPERATOR_CLI_FRIEND_REQUEST_LIST)
        request_state = request_result["data"]
        admission = request_state.get("admission", "ACCEPTED")
        request_id = request_state.get("request_id")
        if admission == "ALREADY_ADMITTED":
            if (request_id is not None
                    or request_state.get("friends_verified") is not True):
                raise RunStateError(
                    "operator CLI friend admission substitution is not verified")
        elif not isinstance(request_id, str) or not request_id:
            raise RunStateError("operator CLI friend admission did not return a request ID")
        accept_result = self.runner.execute(
            OPERATOR_CLI_FRIEND_ACCEPT_LIST, request_id=request_id)
        accept_invoked = accept_result["data"].get("accept_invoked", True)
        list_result = self.runner.execute(OPERATOR_CLI_FRIEND_LIST)
        list_data = list_result["data"]
        if (not isinstance(list_data, dict)
                or list_data.get("friend_list_verified") is not True
                or list_data.get("target_bot") != self.eng_id):
            raise RunStateError("operator CLI friend list did not contain the ENG fixture Bot")
        prerequisite = {"caller": self.driver_id, "target": self.eng_id,
                        "request_id": request_id, "admission": admission,
                        "accept_invoked": accept_invoked}
        commands = [request_result["evidence"]["command_arguments"],
                    list_result["evidence"]["command_arguments"]]
        if accept_invoked:
            commands.insert(1, ["friend", "accept", "--request-id", request_id])
        else:
            prerequisite["friends_verified"] = True
            prerequisite["substitution"] = (
                "station retained this case fixture admission; the accept "
                "route has no pending request to decide")
        data = {"prerequisite_admission": prerequisite,
                "friend_list_verified": True, "target_bot": self.eng_id}
        evidence = dict(request_result["evidence"])
        evidence["cli_operations"] = [
            request_result["evidence"]["operation"],
            "operator_cli.friend_accept_list", list_result["evidence"]["operation"]]
        evidence["operator_cli_commands"] = commands
        evidence["case_class"] = "friend-list"
        evidence["fixture_effect"] = OPERATOR_CLI_EFFECT
        evidence["gaps"] = []
        evidence["defects"] = []
        return self._finish(AdapterCall(data=data, evidence=evidence))


def execute_operator_cli_case(adapter: object) -> CaseExecution:
    """Run the thirteen reviewed operator parent obligations in order."""
    plan = operator_cli_case_plan()
    validate_operator_cli_adapter(adapter)
    if not isinstance(adapter, RemoteOperatorCliAdapter):
        raise RunStateError("operator CLI requires its shared adapter")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    for method_name, requirement_id in zip(
            OPERATOR_CLI_ADAPTER_METHODS, plan["requirement_ids"]):
        if error_type is not None:
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={}, error_type=None))
            continue
        try:
            call = getattr(adapter, method_name)()
            if not isinstance(call, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            call.validate()
            credential_kind = call.evidence.get("credential_kind")
            if (credential_kind not in OPERATOR_CLI_CREDENTIAL_KINDS
                    or call.evidence.get("fixture_effect") != OPERATOR_CLI_EFFECT
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False
                    or call.evidence.get("transport") not in {"PRODUCT_CLI", "PRODUCT_HTTP"}
                    or call.evidence.get("gaps") != []
                    or call.evidence.get("defects") != []
                    or not isinstance(call.data, dict)):
                raise RunStateError(
                    "operator CLI evidence for " + method_name + " is invalid")
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="PASS", data=dict(call.data),
                evidence=dict(call.evidence), error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            detail = record_error_detail(error)
            first_error_detail = detail
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={},
                error_type=error_type, error_gap=getattr(error, "gap", None),
                error_detail=detail))
    execution = CaseExecution(
        case_id=OPERATOR_CLI_CASE_ID, parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed,
        total_count=len(OPERATOR_CLI_ADAPTER_METHODS),
        error_type=error_type,
        error_detail=first_error_detail,
        ready_to_execute=error_type is None)
    execution.validate()
    return execution
