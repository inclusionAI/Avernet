"""Shared public-v1 auto-accept and friend-list slice from the trusted-team Story."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError

FRIENDSHIP_PARENT = "story_user_builds_trusted_team"

CASE_ID = "PUBLIC-FRIEND-CONNECTION-AUTO-ACCEPT-01"
RESOURCE_DISPOSITION = "BOUNDED_OWNED_BOTS_RETAINED_PUBLIC_ACCEPTANCE"
REQUIREMENT_IDS = ("FRIEND.public-accept", "FRIEND.list")
REQUIREMENT_METHODS = (
    ("FRIEND.public-accept", "auto_accept_public_friend_connection",
     "test_friend_auto_accept_public"),
    ("FRIEND.list", "read_established_friend_list", "test_list_friends"),
)
PUBLIC_ACCEPT_OPERATIONS = (
    "friend.public.driver_connections.read",
    "friend.public.create",
    "friend.public.driver_connections.read",
)
PUBLIC_ACCEPT_METHODS = ("GET", "POST", "GET")
PUBLIC_ACCEPT_STATUSES = (200, 201, 200)


@dataclass(frozen=True)
class FriendPublicAcceptCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != FRIENDSHIP_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != tuple(row[2] for row in REQUIREMENT_METHODS)):
            raise RunStateError("public friendship auto-accept case is invalid")


def friend_public_accept_case_plan() -> FriendPublicAcceptCasePlan:
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
        raise RunStateError("public auto-accept selected the wrong parent")
    rows_by_id = {row["id"]: row for row in story["requirements"]}
    selected = [rows_by_id.get(requirement_id)
                for requirement_id in REQUIREMENT_IDS]
    if (any(row is None for row in selected)
            or [row["id"] for row in selected if row is not None]
                != list(REQUIREMENT_IDS)
            or selected[0]["functions"] != ["test_friend_auto_accept_public"]
            or selected[1]["functions"] != ["test_list_friends"]):
        raise RunStateError("public auto-accept source obligations changed")
    plan = FriendPublicAcceptCasePlan(
        case_id=CASE_ID, parent_id=FRIENDSHIP_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=tuple(row["functions"][0] for row in selected),
    )
    plan.validate()
    return plan


def friend_public_accept_adaptability() -> dict:
    plan = friend_public_accept_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["public-accept", "list"],
        "environments": ["singlebox-public-slice", "preprod-public-slice"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The shared public-v1 auto-accept and friend-list contract is "
            "implemented and mock-verified, but real Singlebox or preprod "
            "execution is still required; the approved owned-Bot friendship is retained."
        ),
    }


def validate_friend_public_accept_adapter(adapter: object) -> None:
    for name in ("auto_accept_public_friend_connection", "read_established_friend_list"):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"public auto-accept adapter is missing {name}")


def _validate_public_accept(result: AdapterCall, adapter: object) -> None:
    data, evidence = result.data, result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("fixture_effect")
                != "BOUNDED_OWNED_BOTS_RETAINED_PUBLIC_ACCEPTANCE"
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or not isinstance(evidence.get("requests"), list)
            or len(evidence["requests"]) != 3):
        raise RunStateError("public auto-accept evidence is incomplete")
    requests = evidence["requests"]
    if ([row.get("operation") for row in requests] != list(PUBLIC_ACCEPT_OPERATIONS)
            or [row.get("method") for row in requests] != list(PUBLIC_ACCEPT_METHODS)
            or [row.get("http_status") for row in requests]
                != list(PUBLIC_ACCEPT_STATUSES)
            or any(row.get("credentials_sent") is not True for row in requests)
            or any(row.get("authorization_sent") is not False for row in requests)):
        raise RunStateError("public auto-accept request order or identity is outside the contract")
    if (not isinstance(data, dict)
            or data.get("status") != "approved"
            or data.get("auto_accepted") is not True
            or data.get("friendship_visible_before") is not False
            or data.get("friendship_visible_after") is not True
            or data.get("request_count") != 2
            or data.get("edge_count") != 2):
        raise RunStateError("public auto-accept result is not exact proof")
    if (data.get("from_actor_id") != adapter.driver_actor_id
            or data.get("to_actor_id") != adapter.member_actor_id):
        raise RunStateError(
            "public auto-accept projection is not the admitted Bot pair")


def _validate_friend_list(result: AdapterCall, adapter: object) -> None:
    data, evidence = result.data, result.evidence
    requests = evidence.get("requests") if isinstance(evidence, dict) else None
    if (not isinstance(requests, list) or len(requests) != 1
            or requests[0].get("operation") != "friend.public.driver_connections.read"
            or requests[0].get("method") != "GET"
            or requests[0].get("http_status") != 200
            or requests[0].get("credentials_sent") is not True
            or requests[0].get("authorization_sent") is not False
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or not isinstance(data, dict)
            or data.get("list_contains_friend") is not True
            or data.get("friend_count") != 1):
        raise RunStateError("public friend-list result is not exact proof")
    if (data.get("driver_actor_id") != adapter.driver_actor_id
            or data.get("friend_actor_id") != adapter.member_actor_id):
        raise RunStateError(
            "established friend list projection is not the admitted Bot pair")


def execute_friend_public_accept_case(adapter: object) -> CaseExecution:
    plan = friend_public_accept_case_plan()
    validate_friend_public_accept_adapter(adapter)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    first_error_detail: str | None = None
    completed = 0
    validators = {
        "auto_accept_public_friend_connection": _validate_public_accept,
        "read_established_friend_list": _validate_friend_list,
    }
    for requirement_id, method_name, _ in REQUIREMENT_METHODS:
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                     method=method_name, status="NOT_RUN",
                                     data=None, evidence={}, error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(f"{method_name} did not return AdapterCall")
            result.validate()
            validators[method_name](result, adapter)
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
        case_id=CASE_ID, parent_id=FRIENDSHIP_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed, total_count=2,
        error_type=error_type, error_detail=first_error_detail,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
