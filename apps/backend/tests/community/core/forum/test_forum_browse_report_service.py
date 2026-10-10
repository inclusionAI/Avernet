from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.forum.models import (
    BrowseReportCreateResult,
    BrowseReportRecord,
)
from agentclaw.community.core.forum.services.forum_service import ForumService


class FakeForumRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_browse_report(
        self,
        *,
        bot_id: str,
        status: str,
        client_request_id: str,
        message: str | None = None,
    ) -> BrowseReportCreateResult:
        now = datetime(2026, 9, 20, tzinfo=timezone.utc)
        self.calls.append(
            {
                "operation": "create_browse_report",
                "bot_id": bot_id,
                "status": status,
                "client_request_id": client_request_id,
                "message": message,
            }
        )
        return BrowseReportCreateResult(
            report=BrowseReportRecord(
                report_id="br_test",
                bot_id=bot_id,
                status=status,
                message=message,
                created_at=now,
                updated_at=now,
            ),
            created=True,
        )


def test_submit_browse_report_normalizes_inputs_and_uppercases_status():
    repo = FakeForumRepository()

    ForumService(repo).submit_browse_report(
        bot_id=" bot-1 ",
        status=" success ",
        client_request_id=" req-1 ",
        message="  finished browse run, no errors  ",
    )

    assert repo.calls == [
        {
            "operation": "create_browse_report",
            "bot_id": "bot-1",
            "status": "SUCCESS",
            "client_request_id": "req-1",
            "message": "finished browse run, no errors",
        }
    ]


@pytest.mark.parametrize("status", ["SUCCESS", "FAILED", "PARTIAL"])
def test_submit_browse_report_accepts_valid_statuses_case_insensitive(status):
    repo = FakeForumRepository()

    ForumService(repo).submit_browse_report(
        bot_id="bot-1",
        status=" " + status.lower() + " ",
        client_request_id="req-x",
    )

    assert repo.calls[0]["status"] == status


def test_submit_browse_report_message_is_optional_none():
    repo = FakeForumRepository()

    ForumService(repo).submit_browse_report(
        bot_id="bot-1", status="FAILED", client_request_id="req-1"
    )

    assert repo.calls[0]["message"] is None


def test_submit_browse_report_rejects_unknown_status():
    repo = FakeForumRepository()

    with pytest.raises(ValidationError) as excinfo:
        ForumService(repo).submit_browse_report(
            bot_id="bot-1", status="boom", client_request_id="req"
        )

    assert "status" in excinfo.value.detail
    assert repo.calls == []


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"bot_id": "  ", "status": "SUCCESS", "client_request_id": "req"}, "bot_id"),
        (
            {"bot_id": "bot-1", "status": "SUCCESS", "client_request_id": "  "},
            "client_request_id",
        ),
        ({"bot_id": "bot-1", "status": "  ", "client_request_id": "req"}, "status"),
    ],
)
def test_submit_browse_report_rejects_empty_required_fields(kwargs, field):
    repo = FakeForumRepository()

    with pytest.raises(ValidationError) as excinfo:
        ForumService(repo).submit_browse_report(**kwargs)

    assert field in excinfo.value.detail
    assert repo.calls == []
