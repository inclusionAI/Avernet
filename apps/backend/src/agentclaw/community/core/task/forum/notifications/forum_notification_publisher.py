"""Aggregate BBS domain events into the recipient notification inbox."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from injector import inject

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.task.domain.identity import (
    is_bare_default_bot_identity,
    parse_scoped_bot_identity,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from .publisher_protocol import ForumNotificationPublisherProtocol

from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderEventType,
)


@dataclass(frozen=True)
class _Actor:
    type: str
    id: str
    display_name: str | None


class ForumNotificationPublisher(ForumNotificationPublisherProtocol):
    """Create open aggregates for BBS recipients.

    The first event creates the notification immediately; subsequent events
    merge into that same unread notification instead of inserting a sibling.
    Once the recipient reads it, the aggregate is closed. Later activity starts
    a fresh notification rather than changing an already-read notice back to
    unread.
    """

    @inject
    def __init__(
        self,
        work_orders: WorkOrderRepositoryProtocol,
        bot_repository: BotRepository,
    ) -> None:
        self._work_orders = work_orders
        self._bot_repository = bot_repository

    def notify(
        self,
        *,
        event_type: WorkOrderEventType,
        recipient_type: str,
        recipient_id: str,
        actor_type: str,
        actor_id: str,
        actor_display_name: str | None,
        target_type: str,
        target_id: str,
        text: str,
        topic_id: str | None,
        post_id: str | None,
        topic_title: str | None = None,
    ) -> None:
        recipient_user_id = self._recipient_user_id(recipient_type, recipient_id)
        if recipient_user_id is None:
            return
        if actor_type == recipient_type and actor_id == recipient_id:
            return
        if actor_type == "HUMAN" and actor_id == recipient_user_id:
            return

        group_id = self._group_id(
            event_type=event_type,
            target_type=target_type,
            target_id=target_id,
            topic_id=topic_id,
            post_id=post_id,
        )
        self._work_orders.merge_notice(
            recipient_user_id=recipient_user_id,
            event_type=event_type.value,
            group_id=group_id,
            biz_type=WorkOrderBizType.BBS.value,
            biz_id=(topic_id or target_id)[:128],
            title=event_type.title,
            content_factory=self._content_factory(
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                actor_display_name=_actor_display_name(
                    actor_type,
                    actor_id,
                    actor_display_name,
                ),
                original_text=text,
                target_type=target_type,
                target_id=target_id,
                topic_id=topic_id,
                topic_title=topic_title,
                post_id=post_id,
            ),
            env=get_current_env(),
        )

    @staticmethod
    def _content_factory(
        *,
        event_type: WorkOrderEventType,
        actor_type: str,
        actor_id: str,
        actor_display_name: str,
        original_text: str,
        target_type: str,
        target_id: str,
        topic_id: str | None,
        topic_title: str | None,
        post_id: str | None,
    ) -> Callable[[dict[str, object] | None], dict[str, object]]:
        def build(current: dict[str, object] | None) -> dict[str, object]:
            aggregate = _decode_aggregate(current)
            actor = _Actor(type=actor_type, id=actor_id, display_name=actor_display_name)
            actors = aggregate.actors
            next_actors = _upsert_actor(actors, actor)
            event_count = aggregate.event_count + 1
            post_ids = list(aggregate.post_ids)
            if post_id and post_id not in post_ids:
                post_ids.append(post_id)
            first_post_id = post_ids[0] if post_ids else post_id
            merged_topic_title = topic_title or aggregate.topic_title
            text = _aggregate_text(
                event_type=event_type,
                original_text=original_text,
                actors=next_actors,
                event_count=event_count,
                topic_title=merged_topic_title,
            )
            return {
                "text": text,
                "target_type": target_type,
                "target_id": target_id,
                "topic_id": topic_id,
                "topic_title": merged_topic_title,
                "first_post_id": first_post_id,
                "latest_post_id": post_id,
                "post_ids": post_ids,
                "event_count": event_count,
                "actor_count": len(next_actors),
                "actors": [actor_to_payload(actor) for actor in next_actors],
                "target": {
                    "topicId": topic_id,
                    "postId": first_post_id,
                },
            }

        return build

    @staticmethod
    def _group_id(
        *,
        event_type: WorkOrderEventType,
        target_type: str,
        target_id: str,
        topic_id: str | None,
        post_id: str | None,
    ) -> str:
        if topic_id:
            identity = f"topic:{topic_id}"
        elif target_type == "POST" and post_id:
            identity = f"post:{post_id}"
        else:
            identity = f"{target_type}:{target_id}"
        raw = f"{event_type.value}|{identity}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _recipient_user_id(
        self,
        recipient_type: str,
        recipient_id: str,
    ) -> str | None:
        if recipient_type == "HUMAN":
            return recipient_id
        if recipient_type == "BOT":
            scoped = parse_scoped_bot_identity(recipient_id)
            if scoped is None:
                if is_bare_default_bot_identity(recipient_id):
                    raise LookupError(
                        "notification recipient default Bot must be owner-scoped"
                    )
                bot = self._bot_repository.get_by_id(recipient_id)
            else:
                bot_id, owner_id = scoped
                bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
            if bot is None:
                raise LookupError("notification recipient bot not found")
            owner_id = bot.get("owner_id")
            if not isinstance(owner_id, str) or not owner_id.strip():
                raise LookupError("notification recipient bot has no owner")
            return owner_id.strip()
        raise LookupError(f"unsupported notification recipient type: {recipient_type}")


@dataclass(frozen=True)
class _Aggregate:
    event_count: int = 0
    actors: tuple[_Actor, ...] = ()
    post_ids: tuple[str, ...] = ()
    topic_title: str | None = None


def _decode_aggregate(current: dict[str, object] | None) -> _Aggregate:
    if not current:
        return _Aggregate()
    event_count = current.get("event_count")
    raw_actors = current.get("actors")
    raw_post_ids = current.get("post_ids")
    topic_title = current.get("topic_title")
    actors: list[_Actor] = []
    if isinstance(raw_actors, list):
        for raw_actor in raw_actors:
            if not isinstance(raw_actor, dict):
                continue
            actor_type = raw_actor.get("type")
            actor_id = raw_actor.get("id")
            display_name = raw_actor.get("displayName")
            if isinstance(actor_type, str) and isinstance(actor_id, str):
                actors.append(
                    _Actor(
                        type=actor_type,
                        id=actor_id,
                        display_name=(
                            display_name if isinstance(display_name, str) else None
                        ),
                    )
                )
    elif isinstance(current.get("actor_id"), str):
        actor = _Actor(
            type=str(current.get("actor_type", "HUMAN")),
            id=str(current["actor_id"]),
            display_name=(
                value
                if isinstance(value := current.get("actor_display_name"), str)
                else None
            ),
        )
        actors.append(actor)
    post_ids = tuple(
        value
        for value in (raw_post_ids if isinstance(raw_post_ids, list) else ())
        if isinstance(value, str)
    )
    return _Aggregate(
        event_count=event_count if isinstance(event_count, int) and event_count > 0 else 0,
        actors=tuple(actors),
        post_ids=post_ids,
        topic_title=topic_title if isinstance(topic_title, str) else None,
    )


def _upsert_actor(actors: tuple[_Actor, ...], next_actor: _Actor) -> tuple[_Actor, ...]:
    result: list[_Actor] = []
    for actor in actors:
        if actor.type == next_actor.type and actor.id == next_actor.id:
            display_name = next_actor.display_name or actor.display_name
            result.append(_Actor(type=actor.type, id=actor.id, display_name=display_name))
        else:
            result.append(actor)
    if not any(actor.type == next_actor.type and actor.id == next_actor.id for actor in result):
        result.append(next_actor)
    return tuple(result)


def _actor_display_name(
    actor_type: str,
    actor_id: str,
    display_name: str | None,
) -> str:
    if display_name and display_name.strip():
        return display_name
    if actor_type == "BOT" and is_bare_default_bot_identity(actor_id):
        scoped = parse_scoped_bot_identity(actor_id)
        if scoped is not None:
            return scoped[0]
        return "Bot"
    return actor_id


def _actor_label(actor: _Actor) -> str:
    if actor.type == "BOT":
        return actor.display_name or actor.id
    return actor.display_name or actor.id



def _aggregate_action(
    event_type: WorkOrderEventType,
    original_text: str,
) -> str:
    action = {
        WorkOrderEventType.BBS_FOLLOWED: "关注了你",
        WorkOrderEventType.BBS_LIKED: "赞了你的内容",
        WorkOrderEventType.BBS_FAVORITED: "收藏了你的内容",
        WorkOrderEventType.BBS_HELP_RESPONDED: "响应了你的求助帖",
    }.get(event_type)
    if action:
        return action
    if original_text == "回复了你的回复":
        return "回复了你的回复"
    return "回复了你的帖子"

def _aggregate_text(
    *,
    event_type: WorkOrderEventType,
    original_text: str,
    actors: tuple[_Actor, ...],
    event_count: int,
    topic_title: str | None,
) -> str:
    actor_labels = [_actor_label(actor) for actor in actors]
    if not actor_labels:
        raise ValueError("aggregate notification requires at least one actor")
    suffix_placeholder = f"《{topic_title}》" if topic_title else ""
    if event_count == 1:
        return f"{actor_labels[0]}{original_text}{suffix_placeholder}"
    topic_suffix = f"《{topic_title}》" if topic_title else ""
    action = _aggregate_action(event_type, original_text)
    if len(actor_labels) == 1:
        return f"{actor_labels[0]}{action}{topic_suffix}（{event_count} 条）"
    visible = "、".join(actor_labels[:2])
    if len(actor_labels) > 2:
        return f"{visible} 等 {len(actor_labels)} 人{action}{topic_suffix}（{event_count} 条）"
    return f"{visible}{action}{topic_suffix}（{event_count} 条）"


def actor_to_payload(actor: _Actor) -> dict[str, str]:
    return {
        "type": actor.type,
        "id": actor.id,
        "displayName": _actor_display_name(actor.type, actor.id, actor.display_name),
    }
