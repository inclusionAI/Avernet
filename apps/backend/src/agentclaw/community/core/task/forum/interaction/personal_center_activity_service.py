"""Application service for the scope-aware BBS personal-center activity list."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.task.forum_personal_center_activity import (
    ForumPersonalCenterActivityRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    ForumInteractionTargetType,
    PersonalCenterActivityPage,
    PersonalCenterActivityQuery,
    PersonalCenterActivityType,
    PersonalCenterActorScope,
)
from agentclaw.community.core.task.forum.interaction.personal_center_protocol import (
    ForumPersonalCenterActivityServiceProtocol,
)
from agentclaw.community.core.task.forum.service_protocol import (
    ForumServiceProtocol,
)

_SUBSCRIPTION_PAGE_SIZE = 100


class ForumPersonalCenterActivityService(ForumPersonalCenterActivityServiceProtocol):
    """Build the authorized actor set from BBS subscriptions, then page once.

    A BOT/ALL scope never reads the generic Bot ownership directory. The
    authoritative source is the BBS browse subscription queried through the
    existing BBS content Service API.
    """

    @inject
    def __init__(
        self,
        forum_service: ForumServiceProtocol,
        repository: ForumPersonalCenterActivityRepositoryProtocol,
    ) -> None:
        self._forum_service = forum_service
        self._repository = repository

    def get_personal_center_activities(
        self, query: PersonalCenterActivityQuery
    ) -> PersonalCenterActivityPage:
        owner_user_id = self._required_owner(query.owner_user_id)
        actor_scope = self._actor_scope(query.actor_scope)
        activity_type = self._activity_type(query.activity_type)
        target_type = self._target_type(query.target_type)
        self._validate_pagination(query.page, query.page_size)

        actors: list[ForumActorRef] = []
        if actor_scope is not PersonalCenterActorScope.BOT:
            actors.append(ForumActorRef(type=ForumActorType.HUMAN, id=owner_user_id))
        if actor_scope is not PersonalCenterActorScope.HUMAN:
            actors.extend(
                ForumActorRef(type=ForumActorType.BOT, id=bot_id)
                for bot_id in self._subscription_bot_ids(owner_user_id)
            )

        if not actors:
            return PersonalCenterActivityPage(total=0, items=())

        return self._repository.list_activities(
            actors=tuple(actors),
            actor_scope=actor_scope,
            activity_type=activity_type,
            target_type=target_type,
            page=query.page,
            page_size=query.page_size,
        )

    def _subscription_bot_ids(self, owner_user_id: str) -> tuple[str, ...]:
        page = 1
        bot_ids: list[str] = []
        total = 0
        while True:
            result = self._forum_service.list_subscriptions(
                page=page,
                page_size=_SUBSCRIPTION_PAGE_SIZE,
                owner_user_id=owner_user_id,
            )
            total = result.total
            for subscription in result.items:
                if subscription.bot_id not in bot_ids:
                    bot_ids.append(subscription.bot_id)
            if not result.items:
                break
            if total <= len(bot_ids):
                break
            if page * _SUBSCRIPTION_PAGE_SIZE >= total:
                break
            page += 1
        if len(bot_ids) > total:
            bot_ids = bot_ids[:total]
        return tuple(bot_ids)

    @staticmethod
    def _required_owner(value: str) -> str:
        if not isinstance(value, str):
            raise ValidationError("owner_user_id must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError("owner_user_id must not be empty")
        if len(normalized) > 256:
            raise ValidationError(
                "owner_user_id must contain no more than 256 characters"
            )
        return normalized

    @staticmethod
    def _actor_scope(value: str) -> PersonalCenterActorScope:
        if not isinstance(value, str):
            raise ValidationError("actor_scope must be a string")
        try:
            return PersonalCenterActorScope(value.strip().upper())
        except ValueError:
            allowed = ", ".join(item.value for item in PersonalCenterActorScope)
            raise ValidationError(f"actor_scope must be one of: {allowed}") from None

    @staticmethod
    def _activity_type(value: str) -> PersonalCenterActivityType:
        if not isinstance(value, str):
            raise ValidationError("activity_type must be a string")
        try:
            return PersonalCenterActivityType(value.strip().upper())
        except ValueError:
            allowed = ", ".join(item.value for item in PersonalCenterActivityType)
            raise ValidationError(f"activity_type must be one of: {allowed}") from None

    @staticmethod
    def _target_type(value: str | None) -> ForumInteractionTargetType | None:
        if value is None:
            return None
        normalized = value.strip().upper() if isinstance(value, str) else None
        if normalized == "ALL":
            return None
        if normalized not in {
            ForumInteractionTargetType.TOPIC.value,
            ForumInteractionTargetType.POST.value,
            ForumInteractionTargetType.HUMAN.value,
            ForumInteractionTargetType.BOT.value,
        }:
            raise ValidationError(
                "target_type must be one of: ALL, TOPIC, POST, HUMAN, BOT"
            )
        assert normalized is not None
        return ForumInteractionTargetType(normalized)

    @staticmethod
    def _validate_pagination(page: int, page_size: int) -> None:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("page must be a positive integer")
        if not isinstance(page_size, int) or isinstance(page_size, bool):
            raise ValidationError("page_size must be an integer")
        if not 1 <= page_size <= 100:
            raise ValidationError("page_size must be between 1 and 100")
