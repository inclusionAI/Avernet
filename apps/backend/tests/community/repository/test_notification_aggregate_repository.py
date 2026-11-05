from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.work_orders.notification import (
    _WorkOrderNotificationRepository,
)
from agentclaw.community.core.work_orders.errors import WorkOrderInvalidEventError
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderNotificationGroupModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env

import agentclaw.community.core.work_orders.repository.models  # noqa: F401


class InMemorySqliteDB:
    def __init__(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    transactional_orm_session = orm_session


@pytest.fixture
def db():
    return InMemorySqliteDB()


@pytest.fixture
def repository(db):
    return _WorkOrderNotificationRepository(db)


def _merge(repository: _WorkOrderNotificationRepository, *, count: int = 1) -> int:
    def factory(current: dict[str, object] | None) -> dict[str, object]:
        return {**current, "event_count": count} if current else {"event_count": count}

    return repository.merge_notice(
        recipient_user_id="900003",
        event_type="BBS_REPLIED",
        group_id="topic-1",
        biz_type="BBS",
        biz_id="topic-1",
        title="社区回复通知",
        content_factory=factory,
        env=get_current_env(),
    )


def test_merge_notice_reuses_one_unread_notification_id(db, repository) -> None:
    with avernet_tenant_scope("teamclaw"):
        first_id = _merge(repository, count=1)
        second_id = _merge(repository, count=2)

        assert first_id == second_id
        with db.orm_session() as session:
            assert session.query(WorkOrderNotificationModel).count() == 1
            assert session.query(WorkOrderNotificationGroupModel).count() == 1
            row = session.get(WorkOrderNotificationModel, first_id)
            assert row is not None
            assert row.is_read is False
            assert json.loads(row.content)["event_count"] == 2


def test_read_notice_becomes_immutable_and_next_event_opens_new_group(
    db, repository
) -> None:
    with avernet_tenant_scope("teamclaw"):
        first_id = _merge(repository, count=1)
        repository.mark_notification_read(
            notification_id=first_id,
            recipient_user_id="900003",
            env=get_current_env(),
        )
        second_id = _merge(repository, count=1)

        assert second_id != first_id
        with db.orm_session() as session:
            assert session.query(WorkOrderNotificationModel).count() == 2
            assert session.query(WorkOrderNotificationGroupModel).count() == 1
            assert session.get(WorkOrderNotificationGroupModel, 1).notification_id == second_id
            first = session.get(WorkOrderNotificationModel, first_id)
            second = session.get(WorkOrderNotificationModel, second_id)
            assert first.is_read is True
            assert second.is_read is False


def test_merge_notice_retries_unique_constraint_conflicts(
    db, repository, monkeypatch
) -> None:
    attempts: list[dict[str, object]] = []

    def flaky_merge(**kwargs):
        attempts.append(dict(kwargs))
        if len(attempts) < 3:
            raise IntegrityError("statement", {}, RuntimeError("conflict"))
        return 3

    monkeypatch.setattr(repository, "_merge_notice_once", flaky_merge)

    result = repository.merge_notice(
        recipient_user_id="900003",
        event_type="BBS_REPLIED",
        group_id="retry-id",
        biz_type="BBS",
        biz_id="topic-1",
        title="社区回复通知",
        content_factory=lambda current: {"event_count": 1},
        env=get_current_env(),
    )

    assert result == 3
    assert len(attempts) == 3


def test_stale_read_group_is_closed_before_creating_new_notice(db, repository) -> None:
    with avernet_tenant_scope("teamclaw"):
        read_id = _merge(repository, count=1)
        with db.orm_session() as session:
            notification = session.get(WorkOrderNotificationModel, read_id)
            assert notification is not None
            notification.is_read = True

        with db.orm_session() as session:
            assert session.query(WorkOrderNotificationGroupModel).count() == 1

        new_id = _merge(repository, count=1)

        with db.orm_session() as session:
            assert new_id != read_id
            rows = session.query(WorkOrderNotificationGroupModel).all()
            assert len(rows) == 1
            assert rows[0].notification_id == new_id


def test_merge_notice_rejects_non_object_aggregate_content(db, repository) -> None:
    with avernet_tenant_scope("teamclaw"):
        _merge(repository, count=1)

        with pytest.raises(
            WorkOrderInvalidEventError, match="aggregate notice content must be an object"
        ):
            repository.merge_notice(
                recipient_user_id="900003",
                event_type="BBS_REPLIED",
                group_id="topic-1",
                biz_type="BBS",
                biz_id="topic-1",
                title="社区回复通知",
                content_factory=lambda _current: [],
                env=get_current_env(),
            )


def test_decode_notice_content_rejects_empty_or_non_object_payload(repository) -> None:
    assert repository._decode_notice_content(None) is None
    assert repository._decode_notice_content(" ") is None
    with pytest.raises(
        WorkOrderInvalidEventError, match="aggregate notice content must be a JSON object"
    ):
        repository._decode_notice_content("[]")


def test_mark_all_notifications_read_closes_groups(db, repository) -> None:
    with avernet_tenant_scope("teamclaw"):
        first_id = _merge(repository, count=1)

        result = repository.mark_all_notifications_read(
            recipient_user_id="900003", env=get_current_env()
        )

        assert result == 1
        with db.orm_session() as session:
            assert session.query(WorkOrderNotificationGroupModel).count() == 0
            assert session.get(WorkOrderNotificationModel, first_id).is_read is True
