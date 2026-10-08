from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.feedback.models import (
    FeedbackCreateResult,
    FeedbackPage,
    FeedbackRecord,
)
from agentclaw.community.core.feedback.services.feedback_service import FeedbackService


class FakeFeedbackRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_feedback(self, **kwargs):
        self.calls.append({"operation": "create_feedback", **kwargs})
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        return FeedbackCreateResult(
            feedback=FeedbackRecord(
                id=42,
                reporter_id=kwargs["reporter_id"],
                module=kwargs["module"],
                content=kwargs["content"],
                created_at=now,
                updated_at=now,
            ),
            created=True,
        )

    def list_feedback(self, **kwargs):
        self.calls.append({"operation": "list_feedback", **kwargs})
        return FeedbackPage(total=0, items=())


def test_create_feedback_normalizes_fields():
    repo = FakeFeedbackRepository()

    FeedbackService(repo).create_feedback(
        reporter_id="  149844  ",
        module="  bbs  ",
        content="  some feedback  ",
    )

    assert repo.calls == [
        {
            "operation": "create_feedback",
            "reporter_id": "149844",
            "module": "bbs",
            "content": "some feedback",
        }
    ]


@pytest.mark.parametrize("field", ["reporter_id", "module", "content"])
def test_create_feedback_rejects_empty_after_trim(field):
    repo = FakeFeedbackRepository()
    base = {"reporter_id": "149844", "module": "bbs", "content": "ok"}
    base[field] = "   "

    with pytest.raises(ValidationError):
        FeedbackService(repo).create_feedback(**base)


def test_create_feedback_rejects_non_string():
    repo = FakeFeedbackRepository()

    with pytest.raises(ValidationError):
        FeedbackService(repo).create_feedback(reporter_id=149844, module="bbs", content="ok")


def test_create_feedback_rejects_oversized_content():
    repo = FakeFeedbackRepository()

    with pytest.raises(ValidationError):
        FeedbackService(repo).create_feedback(
            reporter_id="1", module="bbs", content="x" * 4001
        )


def test_list_feedback_passes_filters_and_offset():
    repo = FakeFeedbackRepository()

    FeedbackService(repo).list_feedback(
        module="bbs", reporter_id="149844", page=3, page_size=20
    )

    assert repo.calls == [
        {
            "operation": "list_feedback",
            "module": "bbs",
            "reporter_id": "149844",
            "offset": 40,
            "limit": 20,
        }
    ]


def test_list_feedback_normalizes_optional_filters():
    repo = FakeFeedbackRepository()

    FeedbackService(repo).list_feedback(
        module="  bbs  ", reporter_id="  149844  ", page=1, page_size=10
    )

    assert repo.calls[0]["module"] == "bbs"
    assert repo.calls[0]["reporter_id"] == "149844"


def test_list_feedback_none_filters_pass_through():
    repo = FakeFeedbackRepository()

    FeedbackService(repo).list_feedback(module=None, reporter_id=None, page=1, page_size=5)

    assert repo.calls[0]["module"] is None
    assert repo.calls[0]["reporter_id"] is None


@pytest.mark.parametrize(
    "page,page_size",
    [(0, 10), (1, 0), (1, 101), (1, -1)],
)
def test_list_feedback_rejects_bad_pagination(page, page_size):
    repo = FakeFeedbackRepository()

    with pytest.raises(ValidationError):
        FeedbackService(repo).list_feedback(
            module=None, reporter_id=None, page=page, page_size=page_size
        )


def test_create_feedback_oversized_module_rejected():
    repo = FakeFeedbackRepository()

    with pytest.raises(ValidationError):
        FeedbackService(repo).create_feedback(
            reporter_id="1", module="m" * 65, content="ok"
        )
