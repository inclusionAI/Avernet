"""Unit tests for the community approval impls (B7).

NoApprovalWorkflow: no approval-workflow service (callers fall through).
DirectPublishApproval: publishes directly via the callbacks bag.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.plugins.community.approval_workflow import NoApprovalWorkflow
from agentclaw.community.plugins.community.bot_publish_approval import DirectPublishApproval


def test_start_approval_reports_no_workflow():
    result = NoApprovalWorkflow().start_approval(applicant="u", biz_id="b1")
    assert result["success"] is False
    assert result["puid"] is None
    assert result["error_msg"]


def test_query_and_cancel():
    wf = NoApprovalWorkflow()
    assert wf.query_approval_status("puid-1")["success"] is False
    assert wf.cancel_approval("puid-1", "operator") is False


def test_direct_publish_calls_publish_directly():
    callbacks = MagicMock()
    callbacks.publish_directly.return_value = {"published": True}

    result = DirectPublishApproval().publish(
        bot={"id": "b1"},
        ext={},
        bot_id="b1",
        owner_id="o1",
        operator_id="op1",
        operator=None,
        public="public",
        permission_owner="owner",
        friend_approval="N",
        access_mode="default",
        callbacks=callbacks,
    )

    assert result == {"published": True}
    # publishes directly — never touches an approval workflow
    callbacks.publish_directly.assert_called_once()
    kwargs = callbacks.publish_directly.call_args.kwargs
    assert kwargs["bot_id"] == "b1" and kwargs["owner_id"] == "o1"
