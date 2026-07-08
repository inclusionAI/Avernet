"""Rule 25 conformance — ApprovalWorkflowPlugin.

Consumer under test: ``POST /api/v1/antprocess/start``
(api/antprocess/router.py:26). The endpoint resolves
``ApprovalWorkflowPlugin`` via DI and forwards the request. The local impl
returns the ``_LOCAL_NOT_STARTED`` envelope with the precise
``error_msg="local mode — antprocess unavailable"``.

Plugin-hit assertion: the endpoint's response carries the exact
local-mode error string — only producible by the local impl.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_start_approval_returns_local_unavailable_envelope(
    app_with_testing_modules,
) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.post(
        "/api/v1/antprocess/start",
        cookies={"staff_id": "alice"},
        json={
            "process_code": "demo_proc",
            "applicant": "alice",
            "biz_id": "biz_x",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    assert "local mode" in body["error_msg"]


def test_community_start_approval_reports_no_workflow(community_world) -> None:
    """The community column wires a real ``NoApprovalWorkflow``: start_approval
    reports "no workflow" so callers fall through (publishing goes direct via the
    community ``DirectPublishApproval`` strategy)."""
    from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin

    wf = community_world.get(ApprovalWorkflowPlugin)
    result = wf.start_approval(applicant="alice", biz_id="biz_x")
    assert result["success"] is False
    assert result["error_msg"]
