from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from agentclaw.community.core.errors import Forbidden, InternalError, NotFound, ValidationError
from agentclaw.community.core.task.forum.digest.models import (
    ForumDigestReportBrief,
    ForumDigestReportCreateResult,
    ForumDigestReportDecisionResult,
    ForumDigestReportItem,
    ForumDigestReportRecord,
)
from agentclaw.community.core.task.forum.digest.report_service import (
    BbsDigestReportService,
)
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult

BRIEF = ForumDigestReportBrief(
    summary="Bot produced a whole-batch summary before submission.",
    highlights=("Retain the BBS confirmation boundary",),
)
ITEM = ForumDigestReportItem(
    source="BBS",
    topic_id="topic_1",
    post_id="post_1",
    title="Keep BBS rules",
    content="High-value BBS content needs owner confirmation.",
    reason="Affects retention boundaries",
    tags=("bbs",),
    retention_target="long_term_memory",
)


@dataclass
class FakeRepository:
    records: dict[str, ForumDigestReportRecord]

    def create_report(
        self, *, bot_id, owner_user_id, client_request_id, brief, items
    ):
        existing = next(
            (
                record
                for record in self.records.values()
                if record.bot_id == bot_id
                and record.client_request_id == client_request_id
            ),
            None,
        )
        if existing is not None:
            return ForumDigestReportCreateResult(digest=existing, created=False)
        now = datetime.now(timezone.utc)
        record = ForumDigestReportRecord(
            digest_id=f"bbsd-{len(self.records) + 1}",
            bot_id=bot_id,
            owner_user_id=owner_user_id,
            client_request_id=client_request_id,
            brief=brief,
            items=tuple(items),
            status="PENDING_CONFIRMATION",
            notification_status="PENDING",
            created_at=now,
            updated_at=now,
        )
        self.records[record.digest_id] = record
        return ForumDigestReportCreateResult(digest=record, created=True)

    def get_report(self, digest_id):
        return self.records.get(digest_id)

    def get_report_by_request(self, bot_id, client_request_id):
        return next(
            (
                record
                for record in self.records.values()
                if record.bot_id == bot_id
                and record.client_request_id == client_request_id
            ),
            None,
        )

    def update_delivery(
        self, *, digest_id, session_id, session_url, notification_status
    ):
        old = self.records.get(digest_id)
        if old is None:
            return None
        self.records[digest_id] = replace(
            old,
            session_id=session_id,
            session_url=session_url,
            notification_status=notification_status,
            updated_at=datetime.now(timezone.utc),
        )
        return self.records[digest_id]

    def mark_failed(self, digest_id):
        old = self.records.get(digest_id)
        if old is None:
            return None
        self.records[digest_id] = replace(old, status="FAILED")
        return self.records[digest_id]

    def apply_decision(
        self, *, digest_id, decision, decision_client_request_id
    ):
        old = self.records[digest_id]
        if old.status == decision:
            return ForumDigestReportDecisionResult(digest=old, changed=False)
        updated = replace(
            old,
            status=decision,
            decision_client_request_id=decision_client_request_id,
            decided_at=datetime.now(timezone.utc),
        )
        self.records[digest_id] = updated
        return ForumDigestReportDecisionResult(digest=updated, changed=True)


class FakeBotService:
    def get_bot_by_id(self, bot_id: str):
        assert bot_id == "bot-a"
        return {"bot_id": bot_id, "owner_id": "111111", "bot_name": "Bot A"}


class FakeOpenApiBot:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._error = error

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return BotSendResult(run_id="run-1", session_id="session-1")


class FakeNotifySender:
    def __init__(self, *, result: str | None = "notify-1") -> None:
        self.messages: list[tuple[object, str]] = []
        self._result = result

    def send(self, message, *, channel="tc_card"):
        self.messages.append((message, channel))
        return self._result


class FakeFrontendUrl:
    def get(self) -> str:
        return "http://frontend.local"


def service(
    repository,
    *,
    bot_service=None,
    openapi_bot=None,
    notify_sender=None,
):
    return BbsDigestReportService(
        repository=repository,
        bot_service=bot_service or FakeBotService(),
        openapi_bot=openapi_bot if openapi_bot is not None else FakeOpenApiBot(),
        notify_sender=notify_sender or FakeNotifySender(),
        frontend_url_provider=FakeFrontendUrl(),
    )


@pytest.mark.asyncio
async def test_create_report_creates_session_and_bbs_card_data():
    repository = FakeRepository({})
    notify_sender = FakeNotifySender()
    openapi_bot = FakeOpenApiBot()
    svc = BbsDigestReportService(
        repository=repository,
        bot_service=FakeBotService(),
        openapi_bot=openapi_bot,
        notify_sender=notify_sender,
        frontend_url_provider=FakeFrontendUrl(),
    )

    result = await svc.create_report(
        bot_id="bot-a",
        client_request_id="digest-req-1",
        brief=BRIEF,
        items=[ITEM],
    )

    assert result.created is True
    assert result.digest.status == "PENDING_CONFIRMATION"
    assert result.digest.owner_user_id == "111111"
    assert result.digest.session_id == "session-1"
    assert result.digest.session_url == (
        "http://frontend.local/workspace/chat?bot=bot-a%3A111111&session=session-1"
    )
    assert result.digest.notification_status == "SUBMITTED"

    assert len(openapi_bot.calls) == 1
    assert openapi_bot.calls[0]["bot_id"] == "bot-a"
    assert openapi_bot.calls[0]["metadata"]["source"] == "bbs_digest_report"
    assert openapi_bot.calls[0]["metadata"]["digest_id"] == result.digest.digest_id
    assert "摘要: " + BRIEF.summary in openapi_bot.calls[0]["message"]
    assert "- " + BRIEF.highlights[0] in openapi_bot.calls[0]["message"]

    message, channel = notify_sender.messages[0]
    assert channel == "tc_card"
    assert message.recipient == "111111"
    assert message.deep_link == result.digest.session_url
    card_data = json.loads(message.extra["card_data"])
    assert card_data["digest_id"] == result.digest.digest_id
    assert card_data["summary"] == BRIEF.summary
    assert card_data["highlights"] == list(BRIEF.highlights)
    assert card_data["item_count"] == 1
    assert card_data["session_url"] == result.digest.session_url
    assert "first_title" not in card_data


@pytest.mark.asyncio
async def test_create_report_replay_skips_session_and_notification():
    repository = FakeRepository({})
    openapi_bot = FakeOpenApiBot()
    svc = service(repository, openapi_bot=openapi_bot)

    first = await svc.create_report(
        bot_id="bot-a",
        client_request_id="digest-req-1",
        brief=BRIEF,
        items=[ITEM],
    )
    second = await svc.create_report(
        bot_id="bot-a",
        client_request_id="digest-req-1",
        brief=replace(
            BRIEF, summary="Changed summary that must not replace the replay value"
        ),
        items=[replace(ITEM, title="Changed title")],
    )

    assert second.created is False
    assert second.digest.digest_id == first.digest.digest_id
    assert second.digest.brief.summary == BRIEF.summary
    assert len(openapi_bot.calls) == 1
    assert repository.get_report(first.digest.digest_id).status == (
        "PENDING_CONFIRMATION"
    )


@pytest.mark.asyncio
async def test_brief_is_required_and_normalized():
    repository = FakeRepository({})
    svc = service(repository)

    with pytest.raises(ValidationError, match="brief.summary"):
        await svc.create_report(
            bot_id="bot-a",
            client_request_id="digest-req-1",
            brief=ForumDigestReportBrief(summary=" ", highlights=()),
            items=[ITEM],
        )

    assert repository.records == {}


@pytest.mark.asyncio
async def test_missing_bot_owner_fails_without_persisting():
    class MissingBotService:
        def get_bot_by_id(self, _bot_id):
            return None

    repository = FakeRepository({})
    svc = service(
        repository,
        bot_service=MissingBotService(),
    )

    with pytest.raises(NotFound):
        await svc.create_report(
            bot_id="bot-a",
            client_request_id="digest-req-1",
            brief=BRIEF,
            items=[ITEM],
        )

    assert repository.records == {}


@pytest.mark.asyncio
async def test_session_creation_failure_marks_batch_failed():
    repository = FakeRepository({})
    svc = service(
        repository,
        openapi_bot=FakeOpenApiBot(error=RuntimeError("baas unavailable")),
    )

    with pytest.raises(InternalError):
        await svc.create_report(
            bot_id="bot-a",
            client_request_id="digest-req-1",
            brief=BRIEF,
            items=[ITEM],
        )

    digest = repository.get_report_by_request("bot-a", "digest-req-1")
    assert digest is not None
    assert digest.status == "FAILED"
    assert digest.session_id is None


@pytest.mark.asyncio
async def test_notification_port_failure_is_persisted():
    repository = FakeRepository({})
    svc = service(repository, notify_sender=FakeNotifySender(result=None))

    result = await svc.create_report(
        bot_id="bot-a",
        client_request_id="digest-req-1",
        brief=BRIEF,
        items=[ITEM],
    )

    assert result.digest.status == "PENDING_CONFIRMATION"
    assert result.digest.notification_status == "FAILED"
    assert result.digest.session_id == "session-1"


@pytest.mark.asyncio
async def test_decision_is_bot_scoped_and_batch_idempotent():
    repository = FakeRepository({})
    svc = service(repository)
    created = await svc.create_report(
        bot_id="bot-a",
        client_request_id="digest-req-1",
        brief=BRIEF,
        items=[ITEM],
    )

    confirmed = await svc.report_decision(
        digest_id=created.digest.digest_id,
        bot_id="bot-a",
        decision="CONFIRMED",
        decision_client_request_id="decision-req-1",
    )
    replay = await svc.report_decision(
        digest_id=created.digest.digest_id,
        bot_id="bot-a",
        decision="CONFIRMED",
        decision_client_request_id="decision-req-1",
    )

    assert confirmed.changed is True
    assert confirmed.digest.status == "CONFIRMED"
    assert replay.changed is False
    assert replay.digest.status == "CONFIRMED"


@pytest.mark.asyncio
async def test_decision_from_another_bot_is_forbidden():
    repository = FakeRepository({})
    svc = service(repository)
    created = await svc.create_report(
        bot_id="bot-a",
        client_request_id="digest-req-1",
        brief=BRIEF,
        items=[ITEM],
    )

    with pytest.raises(Forbidden):
        await svc.report_decision(
            digest_id=created.digest.digest_id,
            bot_id="bot-b",
            decision="REJECTED",
            decision_client_request_id="decision-req-1",
        )
