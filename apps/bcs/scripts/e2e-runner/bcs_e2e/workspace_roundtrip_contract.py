"""Shared legacy-HTTP contract for the dual-environment Workspace round trip.

The round trip drives one owned Group across the shared-workspace,
routing-service and message-exchange obligations: the driver Bot
creates the room, the owning Human proves that both deprecated
Workspace routes are unavailable, the driver Bot wires structured
routing while the owning Human configures the bounded service, the
Human posts a persistent message while the driver Bot exchanges live
chat and agent callbacks, and the Human finalizes the room with an
exact delete followed by a not-found read. Each credential is recorded
per request; the fixture is finalized, not retained.

The shared-workspace, bounded-service settings and agent-callback
routes do not bind a route-level caller in the reviewed OCB HTTP
surface; the case records that as a documented identity gap instead
of an exact-actor admission, while group detail reads stay open
read-only projections.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError
from .write_transport import WriteClient
from .workspace_cli_profile import (
    WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION,
    WORKSPACE_ROUNDTRIP_CHAT_OPERATION,
    WORKSPACE_ROUNDTRIP_CREATE_OPERATION,
    WORKSPACE_ROUNDTRIP_DELETE_OPERATION,
    WORKSPACE_ROUNDTRIP_DETAIL_OPERATION,
    WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION,
    WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION,
    WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION,
    WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION,
    WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
    validate_workspace_roundtrip_profile,
)

WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-ROUNDTRIP-01"
REQUIREMENT_IDS = (
    "WORKSPACE.workspace",
    "WORKSPACE.routing-service",
    "WORKSPACE.messages",
)
CASE_METHODS = (
    "verify_workspace_unavailable",
    "configure_routing_service",
    "exchange_group_messages",
)
FIXTURE_EFFECT = "FINALIZED_BOUNDED_OWNED_BOTS_WORKSPACE_ROUND_TRIP"
SOURCE_FUNCTIONS = (
    "_story_group_metadata_without_workspace",
    "_story_group_metadata_without_workspace",
    "_story_group_metadata_without_workspace",
)
STEP_OPERATIONS = {
    "WORKSPACE.workspace": WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
    "WORKSPACE.routing-service": (
        WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION),
    "WORKSPACE.messages": WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION,
}
ROUNDTRIP_LABEL = "Workspace round trip"
WORKSPACE_DECISIONS = ["ship the hotfix"]
WORKSPACE_NOTES = ["customer impact contained"]
ROUTING_MODE = "structured"
ROUTING_DEFAULT_DELIVERY = "inject_observers"
SERVICE_MAX_CONCURRENCY = 2
PERSISTENT_MESSAGE = "Please validate the hotfix"
LIVE_CHAT_MESSAGE = "@产品经理 please coordinate the release"
AGENT_CALLBACK_MESSAGE = "Engineering validation finished"

# Route-level caller gaps in the reviewed OCB HTTP surface.
ROUTE_CALLER_GAPS = frozenset({
    WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
    WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION,
    WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION,
    WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION,
})
OPEN_READS = frozenset({WORKSPACE_ROUNDTRIP_DETAIL_OPERATION})


def route_caller_binding(operation: object) -> str:
    """Classify a route's caller admission for the evidence trail."""
    if operation in ROUTE_CALLER_GAPS:
        return "documented_gap"
    if operation in OPEN_READS:
        return "open_read"
    return "route_bound"


@dataclass(frozen=True)
class WorkspaceRoundtripCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace round trip case is invalid")


def workspace_roundtrip_case_plan() -> WorkspaceRoundtripCasePlan:
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
        raise RunStateError("workspace round trip selected the wrong parent")
    rows_by_id = {row["id"]: row for row in story["requirements"]}
    expected_functions = dict(zip(REQUIREMENT_IDS, SOURCE_FUNCTIONS))
    for requirement_id in REQUIREMENT_IDS:
        row = rows_by_id.get(requirement_id)
        if row is None or row["functions"][0] != expected_functions[requirement_id]:
            raise RunStateError("workspace round trip source obligation changed")
    if story["migration_status"] != "NOT_MIGRATED":
        raise RunStateError("workspace round trip parent is already migrated")
    plan = WorkspaceRoundtripCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_roundtrip_adaptability() -> dict:
    plan = workspace_roundtrip_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": [
            "workspace", "routing_service", "messages"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-http-legacy-groups",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The legacy Group Workspace round trip HTTP contract is "
            "implemented and mock-verified, but real Singlebox or "
            "preprod effect admission and execution remain independent "
            "gates; the round-trip Group is finalized exactly, not "
            "retained."
        ),
    }


def validate_workspace_roundtrip_adapter(adapter: object) -> None:
    for name in CASE_METHODS:
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(
                f"workspace round trip adapter is missing {name}")


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
            f"workspace round trip evidence for {case_class} "
            "is outside the contract")
    for record, kind in zip(requests, credential_kinds):
        if (not isinstance(record, dict)
                or record.get("method") not in {"GET", "POST", "PUT",
                                                "PATCH", "DELETE"}
                or not isinstance(record.get("path"), str)
                or not record["path"]
                or not isinstance(record.get("submitted_request_id"), str)
                or record.get("credential_kind") != kind
                or record.get("route_caller_binding")
                != route_caller_binding(record.get("operation"))):
            raise RunStateError(
                "workspace round trip request record is outside the "
                "contract")


def _validate_created(data: object, group_id: str, driver: str,
                      member: str, qa: str) -> None:
    if (not isinstance(data, dict)
            or data.get("id") != group_id
            or not isinstance(group_id, str) or not group_id
            or data.get("driver_bot") != driver
            or data.get("participants") != [driver, member, qa]
            or not isinstance(data.get("group_kind"), str)
            or not data["group_kind"]
            or data.get("created") is not True):
        raise RunStateError(
            "workspace round trip creation did not acknowledge the "
            "exact Group")


def _detail_participants(data: object, group_id: str, driver: str,
                         member: str, qa: str) -> list[dict]:
    if (not isinstance(data, dict)
            or data.get("id") != group_id
            or data.get("driver_bot") != driver
            or data.get("label") != ROUNDTRIP_LABEL
            or not isinstance(data.get("group_kind"), str)
            or not data["group_kind"]):
        raise RunStateError(
            "workspace round trip detail read-back differs from the "
            "created Group")
    rows = data.get("participants")
    if not isinstance(rows, list) or len(rows) != 3:
        raise RunStateError("workspace round trip roster is invalid")
    roles: dict[str, str] = {}
    for row in rows:
        if (not isinstance(row, dict)
                or not isinstance(row.get("bot_uuid"), str)
                or not isinstance(row.get("role"), str)):
            raise RunStateError(
                "workspace round trip participant view is invalid")
        roles[row["bot_uuid"]] = row["role"]
    if roles != {driver: "driver", member: "consultant",
                 qa: "consultant"}:
        raise RunStateError(
            "workspace round trip roster differs from the created "
            "Group")
    return rows


def _validate_workspace_unavailable(data: object, record: dict,
                                    group_id: str) -> None:
    if (data is not None
            or record.get("http_status") != 404
            or record.get("operation") not in {
                WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
                WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION}
            or group_id not in record.get("path", "")):
        raise RunStateError(
            "workspace round trip deprecated Workspace state did not "
            "return an exact HTTP 404")


def _validate_routing_policy(data: object) -> None:
    policy = data.get("routing_policy") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("ok") is not True
            or not isinstance(policy, dict)
            or policy.get("mode") != ROUTING_MODE
            or policy.get("default_bot_final_delivery")
            != ROUTING_DEFAULT_DELIVERY):
        raise RunStateError(
            "workspace round trip did not acknowledge the structured "
            "routing policy")


def _validate_settings(data: object, record: dict, group_id: str) -> str:
    # Dev PR #3173 ("retire service session policies and callbacks") removed
    # Group.service_spec policies station-wide; the reviewed preprod build
    # answers PATCH /groups/{id}/settings with HTTP 410 and the transport
    # intentionally drops non-2xx bodies, so the retired form is identified
    # from the record only. Both forms are accepted on every environment
    # and the observed form is reported per run.
    if record.get("http_status") == 410:
        return "retired_410"
    spec = data.get("service_spec") if isinstance(data, dict) else None
    if (not isinstance(data, dict)
            or data.get("id") != group_id
            or not isinstance(spec, dict)
            or spec.get("max_concurrency") != SERVICE_MAX_CONCURRENCY
            or data.get("status") != "ok"):
        raise RunStateError(
            "workspace round trip did not acknowledge the bounded "
            "service settings")
    return "service_spec_ack"


def _validate_message_sent(data: object) -> None:
    routed_to = data.get("routed_to") if isinstance(data, dict) else None
    if (not isinstance(data, dict)
            or not isinstance(data.get("message_id"), str)
            or not data["message_id"]
            or not isinstance(routed_to, list) or not routed_to
            or any(not isinstance(item, str) or not item
                   for item in routed_to)):
        raise RunStateError(
            "workspace round trip persistent message was not "
            "acknowledged with routing targets")


def _validate_live_chat_submission(data: object, group_id: str) -> str:
    # The station accepts the live chat synchronously while the relay to
    # connected recipients runs asynchronously; the sync response may
    # legitimately report delivered=false with an empty projection. Anchor
    # the obligation on the exact Group and report the observed form:
    # either the synchronous relay projection (non-empty delivery_results)
    # or the queued admission projection (non-empty queue deliveries).
    results = data.get("delivery_results") if isinstance(data, dict) else None
    if isinstance(results, list) and results:
        if data.get("group_id") != group_id:
            raise RunStateError(
                "workspace round trip live chat delivery reports the wrong "
                "Group")
        return "sync_relayed"
    admission = data.get("queue_admission") if isinstance(data, dict) else None
    deliveries = (admission.get("deliveries")
                  if isinstance(admission, dict) else None)
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or not isinstance(deliveries, list) or not deliveries):
        raise RunStateError(
            "workspace round trip live chat was not acknowledged with a "
            "routed delivery or queued admission for the exact Group")
    return "async_queued"


def _validate_message_delivery(data: object, group_id: str) -> None:
    results = data.get("delivery_results") if isinstance(data, dict) else None
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or not isinstance(results, list) or not results):
        raise RunStateError(
            "workspace round trip live delivery did not report results "
            "for the exact Group")


def _validate_delete_ack(data: object, group_id: str) -> None:
    if (not isinstance(data, dict)
            or data.get("deleted") is not True
            or data.get("id") != group_id):
        raise RunStateError(
            "workspace round trip deletion did not acknowledge the "
            "exact Group finalization")


def _validate_workspace_result(data: object) -> None:
    if (not isinstance(data, dict)
            or not isinstance(data.get("group_id"), str)
            or not data["group_id"]
            or data.get("workspace_update_unavailable") is not True
            or data.get("workspace_read_unavailable") is not True):
        raise RunStateError(
            "workspace round trip deprecated Workspace result is outside "
            "the contract")


def _validate_routing_service_result(data: object) -> None:
    if (not isinstance(data, dict)
            or data.get("mode") != ROUTING_MODE
            or data.get("default_bot_final_delivery")
            != ROUTING_DEFAULT_DELIVERY
            or data.get("max_concurrency") != SERVICE_MAX_CONCURRENCY
            or data.get("service_status") != "ok"):
        raise RunStateError(
            "workspace round trip routing service result is outside "
            "the contract")


def _validate_exchange_messages_result(data: object, driver: str) -> None:
    if (not isinstance(data, dict)
            or not isinstance(data.get("group_id"), str)
            or not data["group_id"]
            or data.get("persistent_message") is not True
            or data.get("live_chat") is not True
            or data.get("agent_callback") is not True
            or data.get("finalized") is not True
            or data.get("not_found_verified") is not True):
        raise RunStateError(
            "workspace round trip message exchange result is outside "
            "the contract")


def execute_workspace_roundtrip_case(adapter: object) -> CaseExecution:
    plan = workspace_roundtrip_case_plan()
    validate_workspace_roundtrip_adapter(adapter)
    preprod = adapter.profile["environment"]["tier"] == "preprod"
    human_credential = ("human_cookie" if preprod
                        else "owned_local_bot_token")
    driver_credential = adapter._credential_kind("driver")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    for requirement_id, method_name in zip(REQUIREMENT_IDS, CASE_METHODS):
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={},
                                      error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            result.validate()
            if requirement_id == "WORKSPACE.workspace":
                _validate_evidence(
                    result,
                    operation=WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
                    case_class="workspace_update",
                    credential_kind=driver_credential,
                    credential_kinds=[driver_credential, human_credential,
                                      human_credential, human_credential])
                _validate_workspace_result(result.data)
            elif requirement_id == "WORKSPACE.routing-service":
                _validate_evidence(
                    result,
                    operation=WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION,
                    case_class="routing_service",
                    credential_kind=driver_credential,
                    credential_kinds=[driver_credential, human_credential])
                _validate_routing_service_result(result.data)
            else:
                _validate_evidence(
                    result,
                    operation=WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION,
                    case_class="exchange_messages",
                    credential_kind=human_credential,
                    credential_kinds=[human_credential, driver_credential,
                                      human_credential, human_credential,
                                      human_credential])
                _validate_exchange_messages_result(result.data,
                                                   adapter.driver_id)
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="PASS",
                                      data=result.data,
                                      evidence=dict(result.evidence),
                                      error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            detail = record_error_detail(error)
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


class RemoteWorkspaceRoundtripAdapter:
    """Drive the reviewed legacy Group Workspace round trip."""

    def __init__(self, profile: dict):
        self.profile = validate_workspace_roundtrip_profile(profile)
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.qa_id = self.profile["actors"]["qa"]["bot_id"]
        self.group_id: str | None = None
        self.driver_client = WriteClient(
            self._bot_profile(self.profile["auth"]["driver_token_env"]))
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
            # owned driver Bot Bearer token (live evidence
            # ACE-BOT-TOKEN-BEARER; a bare Bot Bearer returns USER_NOT_LOGIN
            # / decisionBy=ACE).
            value["auth"] = {
                "kind": "human-cookie-with-owned-driver-bot-token",
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
        return "human_cookie_with_owned_driver_bot_token"

    def _evidence(self, *calls: tuple[dict, str], operation: str,
                    case_class: str) -> dict:
        requests = []
        credential_kinds = []
        for record, credential in calls:
            kind = self._credential_kind(credential)
            entry = dict(record)
            entry["credential_kind"] = kind
            entry["route_caller_binding"] = route_caller_binding(
                entry.get("operation"))
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
                   id(self.human_client): self.human_client}
        secrets = [secret for writer in writers.values()
                   for secret in writer.headers.values()]
        if any(secret and secret in repr(evidence) for secret in secrets):
            raise RunStateError(
                "workspace round trip evidence must not expose a "
                "credential")
        return evidence

    def _require_group(self) -> str:
        if self.group_id is None:
            raise RunStateError(
                "workspace round trip requires its exact created Group")
        return self.group_id

    def verify_workspace_unavailable(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError(
                "workspace round trip Group creation is a one-shot "
                "operation")
        data, record = self.driver_client.call(
            WORKSPACE_ROUNDTRIP_CREATE_OPERATION,
            body={"driver_bot": self.driver_id,
                  "label": ROUNDTRIP_LABEL,
                  "participants": [
                      {"bot_uuid": self.driver_id, "role": "driver"},
                      {"bot_uuid": self.member_id, "role": "consultant"},
                      {"bot_uuid": self.qa_id, "role": "consultant"}]},
            expected=(200,))
        _validate_created(data, data.get("id", ""), self.driver_id,
                          self.member_id, self.qa_id)
        self.group_id = data["id"]
        detail, detail_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_DETAIL_OPERATION,
            group_id=self.group_id, expected=(200,))
        _detail_participants(detail, self.group_id, self.driver_id,
                             self.member_id, self.qa_id)
        _, update_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
            group_id=self.group_id,
            body={"decisions": list(WORKSPACE_DECISIONS),
                  "notes": list(WORKSPACE_NOTES),
                  "tasks": [], "audit_log": []},
            expected=(404,))
        _validate_workspace_unavailable(
            _, update_record, self.group_id)
        _, read_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION,
            group_id=self.group_id, expected=(404,))
        _validate_workspace_unavailable(_, read_record, self.group_id)
        return AdapterCall(
            data={"group_id": self.group_id,
                  "workspace_update_unavailable": True,
                  "workspace_read_unavailable": True},
            evidence=self._evidence(
                (record, "driver"), (detail_record, "human"),
                (update_record, "human"), (read_record, "human"),
                operation=WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
                case_class="workspace_update"),
        )

    def configure_routing_service(self) -> AdapterCall:
        group_id = self._require_group()
        policy, policy_record = self.driver_client.call(
            WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION,
            group_id=group_id,
            body={"mode": ROUTING_MODE,
                  "default_bot_final_delivery": ROUTING_DEFAULT_DELIVERY,
                  "sender_routes": {self.driver_id: [self.member_id]}},
            expected=(200,))
        _validate_routing_policy(policy)
        settings, settings_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION,
            group_id=group_id,
            body={"service_spec": {"max_concurrency":
                                    SERVICE_MAX_CONCURRENCY}},
            expected=(200, 410))
        settings_form = _validate_settings(settings, settings_record,
                                           group_id)
        return AdapterCall(
            data={"group_id": group_id, "mode": ROUTING_MODE,
                  "default_bot_final_delivery": ROUTING_DEFAULT_DELIVERY,
                  "max_concurrency": SERVICE_MAX_CONCURRENCY,
                  "service_status": "ok",
                  "settings_form": settings_form},
            evidence=self._evidence(
                (policy_record, "driver"), (settings_record, "human"),
                operation=WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION,
                case_class="routing_service"),
        )

    def exchange_group_messages(self) -> AdapterCall:
        group_id = self._require_group()
        message, message_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION,
            group_id=group_id,
            body={"sender": self.driver_id,
                  "content": PERSISTENT_MESSAGE,
                  "message_type": "bot", "role": "user"},
            expected=(200,))
        _validate_message_sent(message)
        chat, chat_record = self.driver_client.call(
            WORKSPACE_ROUNDTRIP_CHAT_OPERATION,
            group_id=group_id,
            body={"message": LIVE_CHAT_MESSAGE,
                  "from": self.driver_id},
            expected=(200,))
        chat_form = _validate_live_chat_submission(chat, group_id)
        callback, callback_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION,
            group_id=group_id,
            body={"message": AGENT_CALLBACK_MESSAGE,
                  "mentions": [self.driver_id],
                  "metadata": {"source": "e2e"}},
            expected=(200,))
        _validate_message_delivery(callback, group_id)
        deleted, deleted_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_DELETE_OPERATION,
            group_id=group_id, expected=(200,))
        _validate_delete_ack(deleted, group_id)
        gone, gone_record = self.human_client.call(
            WORKSPACE_ROUNDTRIP_DETAIL_OPERATION,
            group_id=group_id, expected=(404,))
        if gone is not None or gone_record.get("http_status") != 404:
            raise RunStateError(
                "workspace round trip finalization left the Group "
                "readable")
        return AdapterCall(
            data={"group_id": group_id, "persistent_message": True,
                  "live_chat": True, "chat_delivery_form": chat_form,
                  "agent_callback": True,
                  "finalized": True, "not_found_verified": True},
            evidence=self._evidence(
                (message_record, "human"), (chat_record, "driver"),
                (callback_record, "human"), (deleted_record, "human"),
                (gone_record, "human"),
                operation=WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION,
                case_class="exchange_messages"),
        )
