"""Scope and pagination tests for the BBS personal-center activity service."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.task.forum_personal_center_activity import (
    ForumPersonalCenterActivityRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    PersonalCenterActivityPage,
    PersonalCenterActivityQuery,
    PersonalCenterActivityType,
    PersonalCenterActorScope,
)
from agentclaw.community.core.task.forum.interaction.personal_center_activity_service import (
    ForumPersonalCenterActivityService,
)
from agentclaw.community.core.task.forum.models import (
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
)

_NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


def _subscription(bot_id: str, mode: str) -> BrowseSubscriptionRecord:
    return BrowseSubscriptionRecord(
        bot_id=bot_id,
        owner_user_id="user-a",
        mode=mode,
        note=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


class FakeForumService:
    """A minimal list-subscriptions double for the personal-center service."""

    def __init__(self, subscriptions=("bot-a", "bot-b")) -> None:
        self.subscriptions = [
            _subscription(bot_id, "framework" if index == 0 else "openclaw")
            for index, bot_id in enumerate(subscriptions)
        ]
        self.owner_calls: list[str | None] = []

    def list_subscriptions(self, **kwargs):
        self.owner_calls.append(kwargs.get("owner_user_id"))
        return BrowseSubscriptionPage(
            total=len(self.subscriptions), items=tuple(self.subscriptions)
        )


class FakeRepository(ForumPersonalCenterActivityRepositoryProtocol):
    def __init__(self, result=None) -> None:
        self.calls: list[dict] = []
        self.result = result or PersonalCenterActivityPage(total=0, items=())

    def list_activities(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


class FailingRepository(FakeRepository):
    def list_activities(self, **kwargs):
        raise AssertionError("repository reached with an invalid query")


def _query(
    *,
    actor_scope="ALL",
    activity_type="ALL",
    target_type=None,
    page=1,
    page_size=20,
):
    return PersonalCenterActivityQuery(
        owner_user_id="user-a",
        actor_scope=actor_scope,
        activity_type=activity_type,
        target_type=target_type,
        page=page,
        page_size=page_size,
    )


def _act(repository=None, forum_service=None):
    return ForumPersonalCenterActivityService(
        forum_service=forum_service or FakeForumService(),
        repository=repository or FakeRepository(),
    )


def test_actor_scope_controls_the_actor_identity_set_without_mode_filter():
    repository = FakeRepository()
    forum_service = FakeForumService()
    service = _act(repository, forum_service)

    service.get_personal_center_activities(_query(actor_scope="ALL"))

    assert repository.calls[0]["actors"] == (
        ForumActorRef(type=ForumActorType.HUMAN, id="user-a"),
        ForumActorRef(type=ForumActorType.BOT, id="bot-a"),
        ForumActorRef(type=ForumActorType.BOT, id="bot-b"),
    )
    assert repository.calls[0]["actor_scope"] is PersonalCenterActorScope.ALL
    assert {item.mode for item in forum_service.subscriptions} == {
        "framework",
        "openclaw",
    }


@pytest.mark.parametrize(
    ("scope", "expected_types"),
    [
        ("HUMAN", (ForumActorType.HUMAN,)),
        ("BOT", (ForumActorType.BOT, ForumActorType.BOT)),
    ],
)
def test_selected_identity_scopes_do_not_mix_actor_sets(scope, expected_types):
    repository = FakeRepository()
    service = _act(repository)

    service.get_personal_center_activities(_query(actor_scope=scope))

    assert tuple(ref.type for ref in repository.calls[0]["actors"]) == expected_types


def test_no_subscribed_bot_does_not_default_to_all_bots():
    repository = FakeRepository()
    forum_service = FakeForumService(subscriptions=())
    service = _act(repository, forum_service)

    result = service.get_personal_center_activities(_query(actor_scope="BOT"))

    assert result.total == 0
    assert forum_service.owner_calls == ["user-a"]
    assert repository.calls == []


def test_owner_id_is_scoped_through_list_subscriptions():
    repository = FakeRepository()
    forum_service = FakeForumService()
    service = _act(repository, forum_service)

    service.get_personal_center_activities(_query())

    assert forum_service.owner_calls == ["user-a"]


def test_invalid_activity_type_is_rejected_before_repository():
    with pytest.raises(ValidationError, match="activity_type"):
        _act(FailingRepository()).get_personal_center_activities(
            _query(activity_type="NOPE")
        )


@pytest.mark.parametrize("target_type", ["NOPE", "HUMAN", "BOT"])
def test_invalid_target_type_is_rejected_before_repository(target_type):
    with pytest.raises(ValidationError, match="target_type"):
        _act(FailingRepository()).get_personal_center_activities(
            _query(target_type=target_type)
        )


def test_invalid_owner_scope_is_rejected_before_repository():
    with pytest.raises(ValidationError, match="actor_scope"):
        _act(FailingRepository()).get_personal_center_activities(
            _query(actor_scope="NOPE")
        )


@pytest.mark.parametrize(("page", "page_size"), [(0, 20), (1, 0), (1, 101)])
def test_invalid_pagination_is_rejected_before_repository(page, page_size):
    with pytest.raises(ValidationError, match="page"):
        _act(FailingRepository()).get_personal_center_activities(
            _query(page=page, page_size=page_size)
        )


def test_service_forwards_normalized_activity_type_and_target_type():
    repository = FakeRepository()
    service = _act(repository)

    service.get_personal_center_activities(
        _query(activity_type="VIEWED", target_type="TOPIC")
    )

    assert repository.calls[0]["activity_type"] is PersonalCenterActivityType.VIEWED
    assert repository.calls[0]["target_type"] is not None
