from __future__ import annotations

import pytest

from agentclaw.community.core.errors import Conflict
from agentclaw.community.core.repository.implementations.task.forum import (
    ForumDigestReportRepository,
)
from agentclaw.community.core.task.forum.digest.models import (
    ForumDigestReportBrief,
    ForumDigestReportItem,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumDigestReportModel,
)


BRIEF = ForumDigestReportBrief(
    summary="Bot produced a whole-batch summary before submission.",
    highlights=("Confirmation is whole-batch",),
)


def _items():
    return (
        ForumDigestReportItem(
            source="BBS",
            topic_id="topic_1",
            title="Keep BBS rules",
            content="High-value BBS content needs owner confirmation.",
            reason="Affects retention boundaries",
            tags=("bbs", "retention"),
            retention_target="long_term_memory",
        ),
        ForumDigestReportItem(
            source="bbs",
            topic_id="topic_2",
            post_id="post_2",
            title="Bot style preference",
            content="Bot prefers concise structured answers.",
            reason="Helps preserve persona",
            tags=("persona",),
            retention_target="soul_candidate",
        ),
    )


def _create(repo, *, request_id="req-1", items=None):
    return repo.create_report(
        bot_id="bot-a",
        owner_user_id="111111",
        client_request_id=request_id,
        brief=BRIEF,
        items=items if items is not None else _items(),
    )


def test_create_digest_report_persists_batch(db):
    repo = ForumDigestReportRepository(db)
    result = _create(repo)

    assert result.created is True
    assert result.digest.digest_id.startswith("bbsd_")
    assert result.digest.status == "PENDING_CONFIRMATION"
    assert result.digest.notification_status == "PENDING"
    assert result.digest.session_id is None
    assert [item.title for item in result.digest.items] == [
        "Keep BBS rules",
        "Bot style preference",
    ]
    assert result.digest.items[1].post_id == "post_2"
    assert result.digest.brief == BRIEF

    with db.orm_session() as session:
        row = session.query(ForumDigestReportModel).one()
        assert row.bot_id == "bot-a"
        assert row.owner_user_id == "111111"
        assert row.env is not None
        assert row.avernet_tenant is not None
        assert row.brief_json == {
            "summary": BRIEF.summary,
            "highlights": list(BRIEF.highlights),
        }
        assert len(row.items_json) == 2


def test_digest_model_rejects_malformed_brief_json(db):
    repo = ForumDigestReportRepository(db)
    _create(repo)

    with db.orm_session() as session:
        row = session.query(ForumDigestReportModel).one()
        row.brief_json = {"summary": "", "highlights": []}
        with pytest.raises(
            ValueError, match="brief summary must be a non-empty string"
        ):
            row.to_record()


def test_digest_model_rejects_malformed_items_json(db):
    repo = ForumDigestReportRepository(db)
    _create(repo)

    with db.orm_session() as session:
        row = session.query(ForumDigestReportModel).one()
        row.items_json = [
            {
                "source": "BBS",
                "topic_id": "topic_1",
                "title": "Keep BBS rules",
                "content": None,
                "reason": "Affects retention boundaries",
                "tags": [],
                "retention_target": "long_term_memory",
            }
        ]
        with pytest.raises(ValueError, match="non-empty string content"):
            row.to_record()


def test_create_digest_report_is_idempotent_by_request(db):
    repo = ForumDigestReportRepository(db)
    first = _create(repo)
    second = _create(repo, request_id="req-1")

    assert second.created is False
    assert second.digest.digest_id == first.digest.digest_id
    assert second.digest.status == "PENDING_CONFIRMATION"

    with db.orm_session() as session:
        assert session.query(ForumDigestReportModel).count() == 1


def test_different_bot_can_reuse_the_same_request_id(db):
    repo = ForumDigestReportRepository(db)
    first = repo.create_report(
        bot_id="bot-a",
        owner_user_id="111111",
        client_request_id="same-request",
        brief=BRIEF,
        items=_items(),
    )
    second = repo.create_report(
        bot_id="bot-b",
        owner_user_id="222222",
        client_request_id="same-request",
        brief=BRIEF,
        items=_items(),
    )

    assert first.created is True
    assert second.created is True
    assert first.digest.digest_id != second.digest.digest_id
    assert {first.digest.bot_id, second.digest.bot_id} == {"bot-a", "bot-b"}


def test_update_delivery_persists_session_and_notification_state(db):
    repo = ForumDigestReportRepository(db)
    created = _create(repo)

    updated = repo.update_delivery(
        digest_id=created.digest.digest_id,
        session_id="session-1",
        session_url="http://frontend/workspace/chat?bot=bot-a%3A111111&session=session-1",
        notification_status="SUBMITTED",
    )

    assert updated is not None
    assert updated.session_id == "session-1"
    assert updated.status == "PENDING_CONFIRMATION"
    assert updated.notification_status == "SUBMITTED"


def test_apply_decision_is_final_and_replay_is_idempotent(db):
    repo = ForumDigestReportRepository(db)
    created = _create(repo)

    confirmed = repo.apply_decision(
        digest_id=created.digest.digest_id,
        decision="CONFIRMED",
        decision_client_request_id="decision-req-1",
    )
    replay = repo.apply_decision(
        digest_id=created.digest.digest_id,
        decision="CONFIRMED",
        decision_client_request_id="decision-req-1-repeat",
    )

    assert confirmed.changed is True
    assert confirmed.digest.status == "CONFIRMED"
    assert confirmed.digest.decided_at is not None
    assert replay.changed is False
    assert replay.digest.status == "CONFIRMED"


def test_apply_conflicting_decision_is_rejected(db):
    repo = ForumDigestReportRepository(db)
    created = _create(repo)
    repo.apply_decision(
        digest_id=created.digest.digest_id,
        decision="REJECTED",
        decision_client_request_id="decision-req-1",
    )

    with pytest.raises(Conflict):
        repo.apply_decision(
            digest_id=created.digest.digest_id,
            decision="CONFIRMED",
            decision_client_request_id="decision-req-2",
        )
