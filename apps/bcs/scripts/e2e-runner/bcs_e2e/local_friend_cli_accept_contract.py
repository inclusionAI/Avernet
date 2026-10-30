"""Shared product-CLI contract for the trusted-team CLI acceptance obligation."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError


FRIENDSHIP_PARENT = "story_user_builds_trusted_team"
CASE_ID = "DUAL-FRIEND-CLI-ACCEPT-01"
REQUIREMENT_IDS = ("FRIEND.cli-accept",)
CASE_METHOD = "execute_cli_friend_acceptance"
FIXTURE_EFFECT = "BOUNDED_OWNED_BOTS_RETAINED_CLI_ACCEPTANCE"


@dataclass(frozen=True)
class FriendCliAcceptCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != FRIENDSHIP_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != ("test_friend_flow_via_cli",)):
            raise RunStateError("friendship CLI acceptance case is invalid")


def friend_cli_accept_case_plan() -> FriendCliAcceptCasePlan:
    from pathlib import Path

    from .migration_inventory import validate_contract
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = validate_contract(
        read(root / "migration-contracts.json"),
        source_root=root.parent / "e2e-test",
    )
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == FRIENDSHIP_PARENT), None)
    if story is None:
        raise RunStateError("friendship CLI selected the wrong parent")
    requirement = next((row for row in story["requirements"]
                        if row["id"] == "FRIEND.cli-accept"), None)
    if (requirement is None
            or requirement["functions"] != ["test_friend_flow_via_cli"]
            or story["migration_status"] != "NOT_MIGRATED"):
        raise RunStateError("friendship CLI source obligation changed")
    plan = FriendCliAcceptCasePlan(
        case_id=CASE_ID, parent_id=FRIENDSHIP_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=("test_friend_flow_via_cli",),
    )
    plan.validate()
    return plan


def friend_cli_accept_adaptability() -> dict:
    plan = friend_cli_accept_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["cli-accept"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-friend",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The actual product-CLI contract is implemented and mock-verified, but real "
            "Singlebox or preprod effect admission and execution are still required."
        ),
    }


def validate_friend_cli_accept_adapter(adapter: object) -> None:
    for name in ("read_initial_member_requests", "request_driver_friendship",
                 "select_exact_member_request", "accept_member_friend_request",
                 "read_final_driver_friend_list"):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"friendship CLI adapter is missing {name}")


def _validate_cli_result(result: AdapterCall, *, operation: str, role: str,
                         arguments: tuple[str, ...], credential_kinds: set[str],
                         case_class: str) -> dict:
    data, evidence = result.data, result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_CLI"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != role
            or evidence.get("command_arguments") != list(arguments)
            or evidence.get("credential_kind") not in credential_kinds
            or evidence.get("case_class") != case_class
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or evidence.get("credentials_sent") is not True):
        raise RunStateError(f"friendship CLI evidence for {operation} is outside the contract")
    return data


def _validate_no_pending(data: object, driver: str, member: str) -> None:
    items = data if isinstance(data, list) else None
    if not isinstance(items, list):
        raise RunStateError("initial member request list is not an array")
    matches = [row for row in items if isinstance(row, dict)
               and row.get("from_bot") == driver and row.get("to_bot") == member
               and row.get("status") == "pending"]
    if matches:
        raise RunStateError("the exact driver-to-member pending request already exists")


def _validate_created(data: object, driver: str, member: str) -> str:
    if not isinstance(data, dict) or data.get("success") is not True:
        raise RunStateError("friendship request was not accepted by the server")
    row = data.get("data")
    if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
            or not row["id"] or row.get("from_bot") != driver
            or row.get("to_bot") != member or row.get("status") != "pending"):
        raise RunStateError("friendship request did not create the exact pending relationship")
    return row["id"]


def _validate_selected(data: object, driver: str, member: str, request_id: str) -> None:
    items = data if isinstance(data, list) else None
    if not isinstance(items, list):
        raise RunStateError("member request list is not an array")
    matches = [row for row in items if isinstance(row, dict)
               and row.get("id") == request_id
               and row.get("from_bot") == driver and row.get("to_bot") == member
               and row.get("status") == "pending"]
    if len(matches) != 1:
        raise RunStateError("the exact newly created request is not uniquely pending")


def _validate_accepted(data: object) -> None:
    if (not isinstance(data, dict) or data.get("success") is not True
            or set(data) != {"success"}):
        raise RunStateError("friendship acceptance is not exact")


def _validate_final_list(data: object, member: str) -> None:
    items = data if isinstance(data, list) else None
    if not isinstance(items, list):
        raise RunStateError("driver friend list is not an array")
    matches = [row for row in items if isinstance(row, dict)
               and row.get("bot_uuid") == member]
    if len(matches) != 1:
        raise RunStateError("the final CLI friend list does not contain exactly one member entry")


def execute_friend_cli_accept_case(adapter: object) -> CaseExecution:
    plan = friend_cli_accept_case_plan()
    validate_friend_cli_accept_adapter(adapter)
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    records: list[dict] = []
    request_id = ""
    driver = getattr(adapter, "driver_id", "")
    member = getattr(adapter, "member_id", "")
    try:
        before = adapter.read_initial_member_requests()
        before.validate()
        _validate_cli_result(
            before, operation="friend.requests.before", role="member",
            arguments=("friend", "requests", "--direction", "received", "--status", "pending"),
            credential_kinds={"owned_local_bot_token", "human_cookie"},
            case_class="precheck")
        _validate_no_pending(before.data, driver, member)
        records.append(before.evidence)

        created = adapter.request_driver_friendship()
        created.validate()
        _validate_cli_result(
            created, operation="friend.request", role="driver",
            arguments=("friend", "request", "--bot-uuid", member),
            credential_kinds={"owned_local_bot_token", "human_cookie"},
            case_class="request")
        request_id = _validate_created(created.data, driver, member)
        records.append(created.evidence)

        selected = adapter.select_exact_member_request()
        selected.validate()
        _validate_cli_result(
            selected, operation="friend.requests.select", role="member",
            arguments=("friend", "requests", "--direction", "received", "--status", "pending"),
            credential_kinds={"owned_local_bot_token", "human_cookie"},
            case_class="request")
        _validate_selected(selected.data, driver, member, request_id)
        records.append(selected.evidence)

        accepted = adapter.accept_member_friend_request()
        accepted.validate()
        _validate_cli_result(
            accepted, operation="friend.accept", role="member",
            arguments=("friend", "accept", "--request-id", request_id),
            credential_kinds={"owned_local_bot_token", "human_cookie"},
            case_class="decision")
        _validate_accepted(accepted.data)
        records.append(accepted.evidence)

        final = adapter.read_final_driver_friend_list()
        final.validate()
        _validate_cli_result(
            final, operation="friend.list", role="driver",
            arguments=("friend", "list",),
            credential_kinds={"owned_local_bot_token", "human_cookie"},
            case_class="verify")
        _validate_final_list(final.data, member)
        records.append(final.evidence)
        completed = 1
    except Exception as error:
        error_type = type(error).__name__
        first_error_detail = record_error_detail(error)

    data = {
        "request_id": request_id,
        "from_actor_id": driver,
        "to_actor_id": member,
        "status": "accepted",
        "final_list_contains_member": completed == 1,
    }
    evidence = {
        "transport": "PRODUCT_CLI",
        "fixture_effect": FIXTURE_EFFECT,
        "requests": records,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "request_id": request_id,
    }
    step = StepOutcome(
        requirement_id=REQUIREMENT_IDS[0], method=CASE_METHOD,
        status="PASS" if error_type is None else "NOT_RUN",
        data=data if error_type is None else None,
        evidence=evidence if error_type is None else {},
        error_type=error_type,
        error_detail=first_error_detail,
    )
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=FRIENDSHIP_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
        error_detail=first_error_detail,
    )
    execution.validate()
    return execution
