from __future__ import annotations

from typing import Callable

from agentclaw.community.core.task.forum.notifications.forum_notification_publisher import (
    ForumNotificationPublisher,
    _decode_aggregate,
)
from agentclaw.community.core.work_orders.models import WorkOrderEventType


class FakeWorkOrderRepository:
    def __init__(self) -> None:
        self.content: dict[str, object] | None = None
        self.calls = 0
        self.groups: set[str] = set()

    def merge_notice(
        self,
        *,
        recipient_user_id: str,
        event_type: str,
        group_id: str,
        biz_type: str,
        biz_id: str,
        title: str,
        content_factory: Callable[[dict[str, object] | None], dict[str, object]],
        env: str,
    ) -> int:
        assert recipient_user_id == "900003"
        assert biz_type == "BBS"
        self.calls += 1
        self.groups.add(group_id)
        self.content = content_factory(self.content)
        return self.calls


class FakeBotRepository:
    def __init__(self) -> None:
        self.id_to_bot: dict[str, dict] = {}

    def get_by_id(self, bot_id: str):
        return self.id_to_bot.get(bot_id)

    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        bot = self.id_to_bot.get(f"{bot_id}:{owner_id}")
        return bot or (None if bot_id == "default" else self.id_to_bot.get(bot_id))


def _notify(
    publisher: ForumNotificationPublisher,
    *,
    actor_id: str,
    actor_name: str,
) -> None:
    publisher.notify(
        event_type=WorkOrderEventType.BBS_REPLIED,
        recipient_type="HUMAN",
        recipient_id="900003",
        actor_type="HUMAN",
        actor_id=actor_id,
        actor_display_name=actor_name,
        target_type="POST",
        target_id=actor_id,
        text="回复了你的帖子",
        topic_id="topic-1",
        post_id=f"post-{actor_id}",
        topic_title="聚合通知测试",
    )


def test_first_reply_creates_one_detailed_notice_payload() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())

    _notify(publisher, actor_id="900001", actor_name="张三")

    assert repository.calls == 1
    assert repository.content is not None
    assert repository.content["text"] == "张三回复了你的帖子《聚合通知测试》"
    assert repository.content["event_count"] == 1
    assert repository.content["actor_count"] == 1
    assert repository.content["target"] == {"topicId": "topic-1", "postId": "post-900001"}


def test_five_replies_merge_into_one_unread_notice() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())

    _notify(publisher, actor_id="900001", actor_name="张三")
    _notify(publisher, actor_id="900002", actor_name="李四")
    _notify(publisher, actor_id="900001", actor_name="张三")
    _notify(publisher, actor_id="900004", actor_name="王五")
    _notify(publisher, actor_id="900005", actor_name="赵六")

    assert repository.calls == 5
    assert len(repository.groups) == 1
    assert repository.content is not None
    assert repository.content["text"] == "张三、李四 等 4 人回复了你的帖子《聚合通知测试》（5 条）"
    assert repository.content["event_count"] == 5
    assert repository.content["actor_count"] == 4
    assert repository.content["post_ids"] == [
        "post-900001",
        "post-900002",
        "post-900004",
        "post-900005",
    ]
    assert repository.content["first_post_id"] == "post-900001"
    assert repository.content["latest_post_id"] == "post-900005"
    assert repository.content["target"] == {
        "topicId": "topic-1",
        "postId": "post-900001",
    }


def test_self_action_is_not_notified() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())

    publisher.notify(
        event_type=WorkOrderEventType.BBS_REPLIED,
        recipient_type="HUMAN",
        recipient_id="900003",
        actor_type="HUMAN",
        actor_id="900003",
        actor_display_name="自己",
        target_type="POST",
        target_id="post-1",
        text="回复了你的帖子",
        topic_id="topic-1",
        post_id="post-1",
    )

    assert repository.calls == 0


def test_baseline_aggregates_follow_like_and_favorite_separately() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())

    for event_type, text in (
        (WorkOrderEventType.BBS_FOLLOWED, "关注了你"),
        (WorkOrderEventType.BBS_LIKED, "赞了你的内容"),
        (WorkOrderEventType.BBS_FAVORITED, "收藏了你的内容"),
    ):
        publisher.notify(
            event_type=event_type,
            recipient_type="HUMAN",
            recipient_id="900003",
            actor_type="HUMAN",
            actor_id="900001",
            actor_display_name="张三",
            target_type="TOPIC",
            target_id="topic-1",
            text=text,
            topic_id="topic-1",
            post_id=None,
            topic_title="聚合通知测试",
        )
        publisher.notify(
            event_type=event_type,
            recipient_type="HUMAN",
            recipient_id="900003",
            actor_type="HUMAN",
            actor_id="900002",
            actor_display_name="李四",
            target_type="TOPIC",
            target_id="topic-1",
            text=text,
            topic_id="topic-1",
            post_id=None,
            topic_title="聚合通知测试",
        )

    assert repository.calls == 6
    assert repository.content is not None


def test_bot_recipient_uses_owner_and_skips_owner_self_action() -> None:
    repository = FakeWorkOrderRepository()
    bots = FakeBotRepository()
    bots.id_to_bot = {"bot-b": {"owner_id": "900003"}}
    publisher = ForumNotificationPublisher(repository, bots)

    publisher.notify(
        event_type=WorkOrderEventType.BBS_LIKED,
        recipient_type="BOT",
        recipient_id="bot-b",
        actor_type="HUMAN",
        actor_id="900003",
        actor_display_name="自己",
        target_type="TOPIC",
        target_id="topic-1",
        text="赞了你的内容",
        topic_id="topic-1",
        post_id=None,
    )

    assert repository.calls == 0

    publisher.notify(
        event_type=WorkOrderEventType.BBS_LIKED,
        recipient_type="BOT",
        recipient_id="bot-b",
        actor_type="HUMAN",
        actor_id="900001",
        actor_display_name="张三",
        target_type="POST",
        target_id="post-1",
        text="赞了你的内容",
        topic_id=None,
        post_id="post-1",
    )

    assert repository.calls == 1
    assert repository.content is not None
    assert repository.content["text"] == "张三赞了你的内容"


def test_default_bot_recipient_resolves_owner_scoped_identity() -> None:
    repository = FakeWorkOrderRepository()
    bots = FakeBotRepository()
    bots.id_to_bot = {"default:900003": {"owner_id": "900003", "bot_name": "默认助手"}}
    publisher = ForumNotificationPublisher(repository, bots)

    publisher.notify(
        event_type=WorkOrderEventType.BBS_LIKED,
        recipient_type="BOT",
        recipient_id="default:900003",
        actor_type="HUMAN",
        actor_id="900001",
        actor_display_name="张三",
        target_type="TOPIC",
        target_id="topic-1",
        text="赞了你的内容",
        topic_id="topic-1",
        post_id=None,
        topic_title="聚合通知测试",
    )

    assert repository.calls == 1
    assert repository.content is not None
    assert repository.content["text"] == "张三赞了你的内容《聚合通知测试》"


def test_notice_group_falls_back_to_target_identity_without_topic_or_post() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())

    publisher.notify(
        event_type=WorkOrderEventType.BBS_FOLLOWED,
        recipient_type="HUMAN",
        recipient_id="900003",
        actor_type="HUMAN",
        actor_id="900001",
        actor_display_name="张三",
        target_type="HUMAN",
        target_id="user-owner",
        text="关注了你",
        topic_id=None,
        post_id=None,
    )

    assert repository.calls == 1
    assert len(repository.groups) == 1


def test_content_factory_merges_legacy_and_malformed_actor_payloads() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())

    factory = publisher._content_factory(
        event_type=WorkOrderEventType.BBS_REPLIED,
        actor_type="HUMAN",
        actor_id="900001",
        actor_display_name="张三",
        original_text="回复了你的回复",
        target_type="POST",
        target_id="post-1",
        topic_id="topic-1",
        topic_title=None,
        post_id="post-2",
    )
    result = factory(
        {
            "event_count": 1,
            "actors": [{"type": "HUMAN", "id": "900001", "displayName": None}, "bad"],
            "post_ids": ["post-1", 1],
            "topic_title": "旧标题",
            "actor_id": "900002",
            "actor_type": "HUMAN",
            "actor_display_name": 123,
        }
    )

    assert result["event_count"] == 2
    assert result["actor_count"] == 1
    assert result["post_ids"] == ["post-1", "post-2"]
    assert result["topic_title"] == "旧标题"
    assert result["text"] == "张三回复了你的回复《旧标题》（2 条）"


def test_content_factory_ignores_invalid_actors_and_requires_valid_aggregate() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())
    factory = publisher._content_factory(
        event_type=WorkOrderEventType.BBS_FOLLOWED,
        actor_type="HUMAN",
        actor_id="900001",
        actor_display_name=None,
        original_text="关注了你",
        target_type="TOPIC",
        target_id="topic-1",
        topic_id="topic-1",
        topic_title="聚合通知测试",
        post_id=None,
    )

    assert _decode_aggregate(None) == _decode_aggregate({})
    assert _decode_aggregate(
        {"event_count": -1, "actors": ["bad"], "post_ids": ["post-1"]}
    ).actors == ()

    result = factory({"event_count": 1, "actors": ["bad", {"type": 1, "id": 2}]})

    assert result["event_count"] == 2
    assert result["actor_count"] == 1


def test_two_actor_notice_text_names_both_actors() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())

    _notify(publisher, actor_id="900001", actor_name="张三")
    _notify(publisher, actor_id="900002", actor_name="李四")

    assert repository.content is not None
    assert repository.content["text"] == "张三、李四回复了你的帖子《聚合通知测试》（2 条）"


def test_default_bot_actor_falls_back_to_display_identity() -> None:
    repository = FakeWorkOrderRepository()
    publisher = ForumNotificationPublisher(repository, FakeBotRepository())
    factory = publisher._content_factory(
        event_type=WorkOrderEventType.BBS_REPLIED,
        actor_type="BOT",
        actor_id="default",
        actor_display_name=None,
        original_text="回复了你的帖子",
        target_type="TOPIC",
        target_id="topic-1",
        topic_id="topic-1",
        topic_title="聚合通知测试",
        post_id=None,
    )

    scoped = factory(None)
    assert scoped["actors"] == [{"type": "BOT", "id": "default", "displayName": "Bot"}]
