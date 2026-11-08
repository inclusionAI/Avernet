"""Endpoint coverage for the internal BBS digest confirmation routes."""

from __future__ import annotations

from datetime import datetime, timezone

from agentclaw.community.api.task.forum_digest_service import (
    ForumDigestReportServiceProtocol,
)
from agentclaw.community.core.task.forum.digest.models import (
    DIGEST_NOTIFICATION_STATUS_SUBMITTED,
    DIGEST_REPORT_STATUS_CONFIRMED,
    DIGEST_REPORT_STATUS_PENDING_CONFIRMATION,
    ForumDigestReportBrief,
    ForumDigestReportCreateResult,
    ForumDigestReportDecisionResult,
    ForumDigestReportItem,
    ForumDigestReportRecord,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)


def _digest_record(*, status: str = DIGEST_REPORT_STATUS_PENDING_CONFIRMATION):
    now = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    return ForumDigestReportRecord(
        digest_id="bbsd_endpoint_1",
        bot_id="bot-a",
        owner_user_id="111111",
        client_request_id="digest-req-endpoint-1",
        brief=ForumDigestReportBrief(
            summary="Bot generated one whole-batch summary.",
            highlights=("Whole-batch confirmation is required",),
        ),
        items=(
            ForumDigestReportItem(
                source="BBS",
                topic_id="topic_endpoint_1",
                title="Confirm this high-value BBS item",
                content="The backend generated a confirmation batch.",
                reason="The item is worth retaining",
                tags=("bbs",),
                retention_target="long_term_memory",
            ),
        ),
        status=status,
        notification_status=DIGEST_NOTIFICATION_STATUS_SUBMITTED,
        session_id="session-endpoint-1",
        session_url="http://frontend.local/workspace/chat?session=session-endpoint-1",
        created_at=now,
        updated_at=now,
    )


def _seed_create_report(world):
    """Bind a boundary stand-in for the successful create service result."""

    async def create_report(self, **kwargs):
        return ForumDigestReportCreateResult(digest=_digest_record(), created=True)

    bind_overrides(
        world,
        ForumDigestReportServiceProtocol,
        {"create_report": create_report},
    )


def _seed_report_decision(world):
    """Bind a boundary stand-in for one final whole-batch decision."""

    async def report_decision(self, **kwargs):
        return ForumDigestReportDecisionResult(
            digest=_digest_record(status=DIGEST_REPORT_STATUS_CONFIRMED),
            changed=True,
        )

    bind_overrides(
        world,
        ForumDigestReportServiceProtocol,
        {"report_decision": report_decision},
    )


_CREATE_BODY = {
    "bot_id": "bot-a",
    "client_request_id": "digest-req-endpoint-1",
    "brief": {
        "summary": "Bot generated one whole-batch summary.",
        "highlights": ["Whole-batch confirmation is required"],
    },
    "items": [
        {
            "source": "BBS",
            "topic_id": "topic_endpoint_1",
            "title": "Confirm this high-value BBS item",
            "content": "The backend generated a confirmation batch.",
            "reason": "The item is worth retaining",
            "tags": ["bbs"],
            "retention_target": "long_term_memory",
        }
    ],
}


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/digest-reports",
    scenario="ok_created",
    input=CaseInput(json_body=_CREATE_BODY),
    seed=_seed_create_report,
    expect=ExpectSuccess(status=201),
)
def create_digest_report_ok():
    """Create one backend-owned digest confirmation batch and return 201."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/digest-reports",
    scenario="invalid_body",
    input=CaseInput(json_body={"bot_id": "bot-a"}),
    expect=ExpectError(status=422),
)
def create_digest_report_invalid_body():
    """Reject a digest create request without its required batch fields."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/digest-reports/{digest_id}/decisions",
    scenario="ok_confirmed",
    input=CaseInput(
        path_params={"digest_id": "bbsd_endpoint_1"},
        json_body={
            "bot_id": "bot-a",
            "client_request_id": "decision-req-endpoint-1",
            "decision": "CONFIRMED",
        },
    ),
    seed=_seed_report_decision,
    expect=ExpectSuccess(status=200),
)
def report_digest_decision_ok():
    """Submit the owner's final confirmation for the whole digest batch."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/digest-reports/{digest_id}/decisions",
    scenario="invalid_body",
    input=CaseInput(
        path_params={"digest_id": "bbsd_endpoint_1"},
        json_body={"bot_id": "bot-a"},
    ),
    expect=ExpectError(status=422),
)
def report_digest_decision_invalid_body():
    """Reject a decision request without its required decision fields."""
