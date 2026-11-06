"""Unknown request keys must not affect relay validation or domain projection."""

import pytest

from agentclaw.community.core.task.domain.models import Status
from .test_router import task_client as task_client


@pytest.mark.parametrize("operation", ["search", "dispatch"])
@pytest.mark.parametrize("invalid", ["missing", "blank", "stale"])
def test_unknown_fields_do_not_bypass_relay_validation(task_client, operation, invalid):
    client, svc = task_client
    svc.set_execution_config(
        {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
    )
    svc.set_node_status("t1", "origin", Status.RUNNING)
    svc.set_node_status("t1", "target", Status.PENDING)
    svc.set_node_planned_by("t1", "target", "origin")
    body = {"task_id": "t1", "relay_token": "run-1", "future_parameter": "ignored"}
    if operation == "search":
        body.update(query="q", node_id="origin")
    else:
        body.update(origin_node_id="origin", target_node_id="target")
    if invalid == "missing":
        body.pop("relay_token")
    else:
        body["relay_token"] = " " if invalid == "blank" else "old-generation"
    response = client.post(f"/api/v1/collaboration/tasks/{operation}", json=body)
    assert response.status_code == (409 if invalid == "stale" else 422), response.text
    assert svc.callback.calls == []


def test_unknown_report_fields_are_not_forwarded_to_service(task_client):
    client, svc = task_client
    svc.set_execution_config(
        {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
    )
    svc.set_node_status("t1", "t1", Status.RUNNING)
    response = client.post(
        "/api/v1/collaboration/tasks/callback/report",
        json={
            "task_id": "t1",
            "node_id": "t1",
            "event_id": "extra-test",
            "relay_token": "run-1",
            "event_type": "EXECUTION_RESULT",
            "future_parameter": {"ignored": True},
            "event": {"execution_decision": "DECLINED", "future_parameter": "ignored"},
        },
    )
    assert response.status_code == 200, response.text
    command = next(
        payload for kind, payload in svc.callback.calls if kind == "new_relay_report"
    )
    assert command.event.execution_decision == "DECLINED"
    assert not hasattr(command, "future_parameter")
    assert not hasattr(command.event, "future_parameter")
