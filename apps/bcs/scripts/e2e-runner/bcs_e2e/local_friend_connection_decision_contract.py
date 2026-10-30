"""Shared public-v1 friend-connection decision slice from the trusted-team Story."""

from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError

FRIENDSHIP_PARENT = "story_user_builds_trusted_team"
PUBLIC_OPERATIONS = (
    "friend_connection_decision.reject.create",
    "friend_connection_decision.member_requests.read",
    "friend_connection_decision.reject",
    "friend_connection_decision.driver_connections.read",
    "friend_connection_decision.accept.create",
    "friend_connection_decision.driver_requests.read",
    "friend_connection_decision.accept",
    "friend_connection_decision.driver_connections.read",
)
RESOURCE_DISPOSITION = "BOUNDED_OWNED_BOTS_RETAINED_DECISIONS"

CASE_ID = "PUBLIC-FRIEND-CONNECTION-DECISIONS-01"
REQUIREMENT_IDS = (
    "FRIEND.protected-reject",
    "FRIEND.protected-accept",
)
REQUEST_OPERATIONS = (
    "friend.decision.create_driver_to_member",
    "friend.decision.member_requests.read",
    "friend.decision.reject",
    "friend.decision.driver_connections.read",
    "friend.decision.create_member_to_driver",
    "friend.decision.driver_requests.read",
    "friend.decision.accept",
    "friend.decision.driver_connections.read",
)
REQUEST_METHODS = ("POST", "GET", "POST", "GET", "POST", "GET", "POST", "GET")
REQUEST_STATUSES = (201, 200, 200, 200, 201, 200, 200, 200)


@dataclass(frozen=True)
class FriendConnectionDecisionCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != FRIENDSHIP_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or len(self.source_functions) != 2
                or self.source_functions != (
                    "test_friend_request_reject_protected",
                    "test_friend_request_accept_protected")):
            raise RunStateError("public friend-connection decision case is invalid")


def friend_connection_decision_case_plan() -> FriendConnectionDecisionCasePlan:
    from .migration_inventory import validate_contract
    from .run_state import read
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    contract = validate_contract(
        read(root / "migration-contracts.json"),
        source_root=root.parent / "e2e-test",
    )
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == FRIENDSHIP_PARENT), None)
    if story is None:
        raise RunStateError("friend-connection decision selected the wrong parent")
    rows_by_id = {row["id"]: row for row in story["requirements"]}
    rows = [rows_by_id[requirement_id] for requirement_id in REQUIREMENT_IDS]
    selected = {requirement_id: rows_by_id.get(requirement_id)
                for requirement_id in REQUIREMENT_IDS}
    if ([row["id"] for row in rows] != list(REQUIREMENT_IDS)
            or selected["FRIEND.protected-reject"]["functions"]
                != ["test_friend_request_reject_protected"]
            or selected["FRIEND.protected-accept"]["functions"]
                != ["test_friend_request_accept_protected"]):
        raise RunStateError("friend-connection source obligations changed")
    plan = FriendConnectionDecisionCasePlan(
        case_id=CASE_ID, parent_id=FRIENDSHIP_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=tuple(row["functions"][0] for row in rows),
    )
    plan.validate()
    return plan


def friend_connection_decision_adaptability() -> dict:
    plan = friend_connection_decision_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["protected-reject", "protected-accept"],
        "environments": ["singlebox-public-slice", "preprod-public-slice"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The shared public-v1 route is implemented and contract-tested, but real "
            "Singlebox or preprod effect admission and execution are still required; "
            "rejected request history and the accepted friendship are retained."
        ),
    }


def validate_friend_connection_decision_adapter(adapter: object) -> None:
    expected = ("reject_friend_connection_request",
                "accept_friend_connection_request")
    for name in expected:
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"friend-connection adapter is missing {name}")


def _validate_decision(result: AdapterCall, *, accepted: bool) -> None:
    pair_status = "approved" if accepted else "rejected"
    visible = accepted
    data, evidence = result.data, result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("fixture_effect")
                != "BOUNDED_OWNED_BOTS_RETAINED_DECISIONS"
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or not isinstance(evidence.get("requests"), list)
            or len(evidence["requests"]) != 4):
        raise RunStateError("friend-connection decision evidence is incomplete")
    requests = evidence["requests"]
    operation_index = 0 if not accepted else 4
    expected_operations = REQUEST_OPERATIONS[operation_index:operation_index + 4]
    expected_methods = REQUEST_METHODS[operation_index:operation_index + 4]
    expected_statuses = REQUEST_STATUSES[operation_index:operation_index + 4]
    if ([row.get("operation") for row in requests] != list(expected_operations)
            or [row.get("method") for row in requests] != list(expected_methods)
            or [row.get("http_status") for row in requests]
                != list(expected_statuses)
            or any(row.get("credentials_sent") is not True
                   and row.get("credentials_sent") != True for row in requests)
            or any(row.get("authorization_sent") is not False for row in requests)):
        raise RunStateError("friend-connection request order or identity is outside the contract")
    if (not isinstance(data, dict)
            or data.get("status") != pair_status
            or data.get("friendship_visible") is not visible
            or not isinstance(data.get("request_id"), str) or not data["request_id"]
            or data.get("from_actor_id") == data.get("to_actor_id")
            or not isinstance(data.get("from_actor_id"), str)
            or not isinstance(data.get("to_actor_id"), str)):
        raise RunStateError("friend-connection decision result is not exact proof")


def execute_friend_connection_decision_case(adapter: object) -> CaseExecution:
    plan = friend_connection_decision_case_plan()
    validate_friend_connection_decision_adapter(adapter)
    steps = []
    error_type = None
    first_error_detail: str | None = None
    completed = 0
    for requirement_id, method_name, accepted in (
            ("FRIEND.protected-reject", "reject_friend_connection_request", False),
            ("FRIEND.protected-accept", "accept_friend_connection_request", True)):
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id, method=method_name,
                                      status="NOT_RUN", data=None, evidence={},
                                      error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            result.validate()
            _validate_decision(result, accepted=accepted)
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
