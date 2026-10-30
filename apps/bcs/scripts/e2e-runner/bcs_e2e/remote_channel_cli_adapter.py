"""Dual-environment Channel product-CLI adapter."""

from __future__ import annotations

import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .channel_cli_profile import (
    CHANNEL_CLI_BIND_OPERATION,
    validate_channel_cli_profile,
    CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION,
    CHANNEL_CLI_GROUP_CREATE_OPERATION,
    CHANNEL_CLI_GROUP_DELETE_OPERATION,
    CHANNEL_CLI_LIST_AFTER_OPERATION,
    CHANNEL_CLI_LIST_BEFORE_OPERATION,
    CHANNEL_CLI_SESSION_CREATE_OPERATION,
    CHANNEL_CLI_SESSION_DELETE_OPERATION,
    CHANNEL_CLI_UNBIND_MISSING_OPERATION,
    validate_channel_cli_profile,
)
from .channel_cli_runner import (
    CREATE_GROUP_CONTEXT,
    CREATE_GROUP_TOPIC,
)
from .local_channel_cli_contract import (
    PARENT,
    channel_cli_source_binding,
)
from .run_state import RunStateError
from .write_transport import WriteClient


CHANNEL_CLI_CASE_ID = "DUAL-CHANNEL-CLI-01"
CHANNEL_CLI_EFFECT = (
    "OWNED_CHANNEL_CLI_LIFECYCLE_THEN_EXACT_SESSION_GROUP_DELETE")

# The reviewed Channel parent obligations in execution order: the
# seven product-CLI leaves plus the two member-scoped legacy
# deletions.
CHANNEL_CLI_ADAPTER_METHODS = (
    "bind_disabled_bridge",
    "list_bindings_before",
    "create_cli_group",
    "create_cli_session",
    "read_empty_conversations",
    "unbind_missing_binding",
    "list_bindings_after",
    "delete_cli_session",
    "delete_cli_group",
)

CHANNEL_CLI_FROZEN_OPERATIONS = (
    CHANNEL_CLI_BIND_OPERATION,
    CHANNEL_CLI_LIST_BEFORE_OPERATION,
    CHANNEL_CLI_GROUP_CREATE_OPERATION,
    CHANNEL_CLI_SESSION_CREATE_OPERATION,
    CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION,
    CHANNEL_CLI_UNBIND_MISSING_OPERATION,
    CHANNEL_CLI_LIST_AFTER_OPERATION,
    CHANNEL_CLI_SESSION_DELETE_OPERATION,
    CHANNEL_CLI_GROUP_DELETE_OPERATION,
)

CHANNEL_CLI_CLI_OBLIGATIONS = (
    "bind-redacted",
    "list-before",
    "group-create",
    "session-create",
    "conversation-empty",
    "unbind-missing",
    "list-after",
)


def channel_cli_case_plan() -> dict:
    """Derive the frozen Channel CLI plan from its reviewed source."""
    binding = channel_cli_source_binding()
    requirement_ids = [row["id"] for row in binding["requirements"]]
    if (binding["parent"] != PARENT
            or requirement_ids != [
                "CHANNEL-CLI.bind-redacted",
                "CHANNEL-CLI.list-before",
                "CHANNEL-CLI.group-create",
                "CHANNEL-CLI.session-create",
                "CHANNEL-CLI.conversation-empty",
                "CHANNEL-CLI.unbind-missing",
                "CHANNEL-CLI.list-after",
                "CHANNEL-CLI.session-delete",
                "CHANNEL-CLI.group-delete",
            ]
            or list(CHANNEL_CLI_FROZEN_OPERATIONS) != [
                "channel_cli.bind",
                "channel_cli.list_before",
                "channel_cli.group_create",
                "channel_cli.session_create",
                "channel_cli.conversation_empty",
                "channel_cli.unbind_missing",
                "channel_cli.list_after",
                "channel_cli.session.delete",
                "channel_cli.group.delete",
            ]
            or CHANNEL_CLI_ADAPTER_METHODS != tuple(CHANNEL_CLI_ADAPTER_METHODS)
            or len(set(CHANNEL_CLI_ADAPTER_METHODS)) != len(CHANNEL_CLI_ADAPTER_METHODS)):
        raise RunStateError("Channel CLI source obligation map changed")
    return {
        "case_id": CHANNEL_CLI_CASE_ID,
        "parent_id": PARENT,
        "requirement_ids": requirement_ids,
        "source_commands": [row["command"] for row in binding["requirements"]],
        "source_expectations": [row["expectation"] for row in binding["requirements"]],
        "adapter_methods": list(CHANNEL_CLI_ADAPTER_METHODS),
        "frozen_operations": list(CHANNEL_CLI_FROZEN_OPERATIONS),
    }


def channel_cli_adaptability() -> dict:
    plan = channel_cli_case_plan()
    return {
        "case_id": CHANNEL_CLI_CASE_ID,
        "legacy_id": plan["parent_id"],
        "requirement_ids": plan["requirement_ids"],
        "selected_obligations": list(CHANNEL_CLI_CLI_OBLIGATIONS),
        "cli_operations": plan["frozen_operations"][:7],
        "cleanup_operations": plan["frozen_operations"][7:],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-channel",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-only; the disabled channel-bridge and the run-derived "
            "absent-binding admission remain separate gates. The owned-local "
            "durable empty-SQLite-table admission for the missing-binding "
            "unbind stays with LOCAL-CHANNEL-CLI-01; the remote slice proves "
            "the run-derived absent binding only through the product "
            "acknowledgement. Live Singlebox/preprod effect admission and "
            "real DingTalk bridge configuration are required before any "
            "release credit."),
    }


def validate_channel_cli_adapter(adapter: object) -> None:
    plan = channel_cli_case_plan()
    missing = [name for name in plan["adapter_methods"]
               if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError("channel CLI adapter is missing methods: "
                            + ", ".join(missing))


class RemoteChannelCliAdapter:
    """Run the reviewed Channel parent obligations, then clean up."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_channel_cli_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Channel CLI requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.eng_id = self.profile["actors"]["eng"]["bot_id"]
        self.group_id: str | None = None
        self.initial_session_id: str | None = None
        self.session_id: str | None = None
        self.bindings_before: list | None = None
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        if profile["environment"]["tier"] == "local":
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {
                    "Authorization": profile["auth"]["member_token_env"],
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

    def _step(self, result: dict, case_class: str) -> AdapterCall:
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": case_class,
            "fixture_effect": CHANNEL_CLI_EFFECT,
        })
        return AdapterCall(data=dict(result["data"]), evidence=evidence)

    def bind_disabled_bridge(self) -> AdapterCall:
        """Bind must fail nonzero, redacted, with the disabled-bridge diagnostic."""
        result = self.runner.execute(CHANNEL_CLI_BIND_OPERATION)
        data = result["data"] if isinstance(result, dict) else None
        if data != {"disabled_bridge": True, "secret_redacted": True}:
            raise RunStateError(
                "channel CLI bind did not fail redacted under the disabled bridge")
        return self._step(result, "bind-redacted")

    def list_bindings_before(self) -> AdapterCall:
        """The case account has no binding before the Group lifecycle."""
        result = self.runner.execute(CHANNEL_CLI_LIST_BEFORE_OPERATION)
        data = result["data"] if isinstance(result, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise RunStateError("channel CLI binding list is not an exact page")
        self.bindings_before = data["items"]
        evidence = self._step(result, "list-before").evidence
        return AdapterCall(
            data={"items": len(self.bindings_before), "phantom_free": True},
            evidence=evidence)

    def create_cli_group(self) -> AdapterCall:
        """PM create-group retains context/topic and PM/ENG participants."""
        result = self.runner.execute(CHANNEL_CLI_GROUP_CREATE_OPERATION)
        data = result["data"] if isinstance(result, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or data.get("driver_bot") != self.member_id
                or data.get("participants") != [self.member_id, self.eng_id]
                or data.get("context") != CREATE_GROUP_CONTEXT
                or data.get("topic") != CREATE_GROUP_TOPIC
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"]
                or not data["session_id"].startswith(data["group_id"] + ":")):
            raise RunStateError("channel CLI Group setup is not exact")
        self.group_id = data["group_id"]
        self.initial_session_id = data["session_id"]
        return self._step(result, "group-create")

    def create_cli_session(self) -> AdapterCall:
        """PM session create creates the explicit Session under the Group."""
        if self.group_id is None or self.session_id is not None:
            raise RunStateError(
                "channel CLI Session create must follow Group setup")
        result = self.runner.execute(
            CHANNEL_CLI_SESSION_CREATE_OPERATION, group_id=self.group_id)
        data = result["data"] if isinstance(result, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"]
                or data.get("id") != data.get("session_id")
                or data.get("group_id") != self.group_id
                or data["session_id"] == self.initial_session_id):
            raise RunStateError("channel CLI explicit Session is not exact")
        self.session_id = data["session_id"]
        return self._step(result, "session-create")

    def read_empty_conversations(self) -> AdapterCall:
        """Participating PM reads empty DingTalk conversations for the Session."""
        if self.session_id is None:
            raise RunStateError(
                "channel CLI conversation read must follow Session create")
        result = self.runner.execute(
            CHANNEL_CLI_CONVERSATION_EMPTY_OPERATION,
            session_id=self.session_id)
        data = result["data"] if isinstance(result, dict) else None
        if data != {"items": []}:
            raise RunStateError(
                "channel CLI DingTalk conversation lookup is not empty")
        return self._step(result, "conversation-empty")

    def unbind_missing_binding(self) -> AdapterCall:
        """CEO unbinds only the run-derived absent binding under the bridge."""
        result = self.runner.execute(CHANNEL_CLI_UNBIND_MISSING_OPERATION)
        data = result["data"] if isinstance(result, dict) else None
        if data != {"ok": True, "binding_id": self.runner.missing}:
            raise RunStateError(
                "channel CLI missing-binding unbind acknowledgement is not exact")
        evidence = self._step(result, "unbind-missing").evidence
        evidence["admission"] = "RUN_DERIVED_ABSENT_BINDING"
        return AdapterCall(
            data={"ok": True, "binding_id": self.runner.missing},
            evidence=evidence)

    def list_bindings_after(self) -> AdapterCall:
        """The final list confirms no phantom binding was created."""
        if self.bindings_before is None:
            raise RunStateError(
                "channel CLI final list must follow the initial list")
        result = self.runner.execute(CHANNEL_CLI_LIST_AFTER_OPERATION)
        data = result["data"] if isinstance(result, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(data.get("items"), list)
                or data["items"] != self.bindings_before):
            raise RunStateError(
                "channel CLI binding list changed; a phantom binding exists")
        evidence = self._step(result, "list-after").evidence
        return AdapterCall(
            data={"items": len(data["items"]), "phantom_free": True,
                  "unchanged": True},
            evidence=evidence)

    def _delete(self, operation: str, *, group_id: str = "",
                session_id: str = "", expected_data: dict,
                case_class: str) -> AdapterCall:
        data, record = self.cleanup_client.call(
            operation, group_id=group_id, session_id=session_id,
            expected=(200,))
        if data != expected_data or record.get("http_status") != 200:
            raise RunStateError(
                "channel CLI deletion acknowledgement is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "case_owned_member",
            "credential_kind": self._credential_kind(),
            "case_class": case_class,
            "fixture_effect": CHANNEL_CLI_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def delete_cli_session(self) -> AdapterCall:
        """Exact creator-scoped explicit Session deletion."""
        if self.session_id is None:
            raise RunStateError(
                "channel CLI Session cleanup requires the exact Session ID")
        return self._delete(
            CHANNEL_CLI_SESSION_DELETE_OPERATION,
            session_id=self.session_id,
            expected_data={"deleted": True, "session_id": self.session_id},
            case_class="cleanup-delete")

    def delete_cli_group(self) -> AdapterCall:
        """Exact creator-scoped Group deletion after the child Session."""
        if self.group_id is None:
            raise RunStateError(
                "channel CLI Group cleanup requires the exact Group ID")
        return self._delete(
            CHANNEL_CLI_GROUP_DELETE_OPERATION,
            group_id=self.group_id,
            expected_data={"deleted": True, "id": self.group_id},
            case_class="cleanup-delete")


def execute_channel_cli_case(adapter: object) -> CaseExecution:
    """Run the nine reviewed Channel parent obligations in order."""
    plan = channel_cli_case_plan()
    validate_channel_cli_adapter(adapter)
    if not isinstance(adapter, RemoteChannelCliAdapter):
        raise RunStateError("channel CLI requires its shared adapter")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    for method_name, requirement_id in zip(
            CHANNEL_CLI_ADAPTER_METHODS, plan["requirement_ids"]):
        if error_type is not None:
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={}, error_type=None,
                error_detail=None))
            continue
        try:
            call = getattr(adapter, method_name)()
            if not isinstance(call, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            call.validate()
            credential_kind = adapter._credential_kind()
            if (call.evidence.get("credential_kind") != credential_kind
                    or call.evidence.get("fixture_effect")
                    != CHANNEL_CLI_EFFECT
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False):
                raise RunStateError(
                    "channel CLI evidence for " + method_name + " is invalid")
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="PASS", data=dict(call.data),
                evidence=dict(call.evidence), error_type=None,
                error_detail=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            detail = record_error_detail(error)
            if first_error_detail is None:
                first_error_detail = detail
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={},
                error_type=error_type, error_detail=detail))
    execution = CaseExecution(
        case_id=CHANNEL_CLI_CASE_ID, parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed,
        total_count=len(CHANNEL_CLI_ADAPTER_METHODS),
        error_type=error_type, ready_to_execute=error_type is None,
        error_detail=first_error_detail)
    execution.validate()
    return execution
