from __future__ import annotations

from tests.community.repository.task.test_intake_atomic_creation import (
    _plan_successor,
    _setup,
)
from agentclaw.community.core.task.repository.models import TaskDeliveryIntentModel


def test_exhausted_delivery_intents_are_not_redispatched() -> None:
    db, graph_service, repo, task_info, result = _setup()
    graph_service.initialize_intake_graph(task_info, result.session_id)
    node_id, relay_token = _plan_successor(graph_service)

    with db.orm_session() as session:
        session.query(TaskDeliveryIntentModel).filter_by(node_id=node_id).update(
            {
                "state": "EXHAUSTED",
                "attempt": 100,
                "lease_token": None,
                "lease_until_ms": None,
            }
        )

    assert repo.list_pending_delivery_intents() == []
    assert repo.claim_delivery_intent("task-1", node_id, relay_token) == (
        "EXHAUSTED",
        None,
    )


def test_generation_change_cannot_expand_the_exhausted_retry_budget() -> None:
    db, graph_service, repo, task_info, result = _setup()
    graph_service.initialize_intake_graph(task_info, result.session_id)
    node_id, _ = _plan_successor(graph_service)

    with db.orm_session() as session:
        session.query(TaskDeliveryIntentModel).filter_by(node_id=node_id).update(
            {"state": "EXHAUSTED", "attempt": 100}
        )

    graph = graph_service.query_task_dashboard("task-1")
    successor = next(item for item in graph.tasks if item.node_id == node_id)
    new_token = f"new-token-{successor.node_id}"
    successor.run_info.extend_props["relay_token"] = new_token
    repo.save_graph(
        graph,
        expected_version=repo.get_version("task-1") or 0,
        runtime_status=graph.status,
        action_events=[],
    )

    with db.orm_session() as session:
        intent = session.query(TaskDeliveryIntentModel).one()
        assert intent.relay_token == new_token
        assert intent.state == "PENDING"
        assert intent.attempt == 100
    assert repo.list_pending_delivery_intents() == []
    assert repo.claim_delivery_intent("task-1", node_id, new_token) == (
        "EXHAUSTED",
        None,
    )
