"""Domain tests for BBS interaction state and summaries."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.errors import Forbidden, NotFound, ValidationError
from agentclaw.community.core.repository.protocols.task.forum_interaction import (
    ForumInteractionRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.forum_interaction_service import (
    ForumInteractionService,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    ForumAuthorStatsRecord,
    ForumInteractionActionType,
    ForumInteractionRecord,
    ForumInteractionSummaryQuery,
    ForumInteractionTargetType,
    ForumStatsRecord,
    ForumSummaryScope,
    ForumTargetRef,
    MAX_SUMMARY_BATCH_ITEMS,
)
from agentclaw.community.core.task.forum.interaction.target_verifier_protocol import (
    ForumInteractionTargetVerifierProtocol,
)
from agentclaw.community.core.task.forum.interaction.service_protocol import (
    ForumAuthorStatsServiceProtocol,
)
from agentclaw.community.core.task.forum.models import (
    ForumPostRecord,
    ForumTopicAnalysisRecord,
    ForumTopicRecord,
)


_NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


def _topic(
    *,
    topic_id: str = "topic-1",
    topic_type: str = "HELP",
    author_type: str = "HUMAN",
    author_id: str = "user-a",
) -> ForumTopicRecord:
    return ForumTopicRecord(
        topic_id=topic_id,
        author_type=author_type,
        author_id=author_id,
        title="Topic",
        body="Topic body",
        status="OPEN",
        analysis=ForumTopicAnalysisRecord(),
        created_at=_NOW,
        updated_at=_NOW,
        topic_type=topic_type,
    )


def _post(
    *,
    topic_id: str = "topic-1",
    post_id: str = "post-1",
    reply_to_post_id: str | None = None,
) -> ForumPostRecord:
    return ForumPostRecord(
        post_id=post_id,
        topic_id=topic_id,
        author_type="HUMAN",
        author_id="user-b",
        body="Reply body",
        reply_to_post_id=reply_to_post_id,
        created_at=_NOW,
        updated_at=_NOW,
    )


class FakeRepository(ForumInteractionRepositoryProtocol):
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    def insert_interaction(self, **kwargs):
        self.inserted.append(dict(kwargs))
        return ForumInteractionRecord(
            actor_type=ForumActorType(kwargs["actor_type"]),
            actor_id=kwargs["actor_id"],
            action_type=ForumInteractionActionType(kwargs["action_type"]),
            target_type=ForumInteractionTargetType(kwargs["target_type"]),
            target_id=kwargs["target_id"],
            env="test",
            active=kwargs["active"],
            created_at=_NOW,
            updated_at=_NOW,
        )

    def get_summary(self, **_kwargs):
        raise AssertionError("service must use the batch repository boundary")

    def batch_get_actor_summaries(self, actor_refs):
        return {
            ref: ForumStatsRecord(follow_count=2 * (index + 1))
            for index, ref in enumerate(actor_refs)
        }

    def batch_get_target_summaries(self, target_refs):
        return {
            ref: ForumStatsRecord(
                view_count=3 + index,
                like_count=1,
                reply_participant_count=4
                if ref.type is ForumInteractionTargetType.TOPIC
                else 0,
                helpful_reply_count=5
                if ref.type is ForumInteractionTargetType.TOPIC
                else 0,
            )
            for index, ref in enumerate(target_refs)
        }

    def batch_get_actor_target_summaries(self, pairs):
        return {pair: ForumStatsRecord(like_count=7) for pair in pairs}


class FakeVerifier(ForumInteractionTargetVerifierProtocol):
    def __init__(
        self,
        *,
        topic: ForumTopicRecord | None = _topic(),
        post: ForumPostRecord | None = _post(),
    ) -> None:
        self.topic = topic
        self.post = post
        self.calls: list[tuple[str, str]] = []

    def verify_topic(self, topic_id: str):
        self.calls.append(("topic", topic_id))
        return self.topic

    def verify_post(self, post_id: str):
        self.calls.append(("post", post_id))
        return self.post

    def verify_human(self, human_id: str):
        self.calls.append(("human", human_id))
        return True

    def verify_bot(self, bot_id: str):
        self.calls.append(("bot", bot_id))
        return True


class FakeAuthorStatsService(ForumAuthorStatsServiceProtocol):
    def __init__(self) -> None:
        self.batch_actors: tuple[ForumActorRef, ...] = ()
        self.single_actors: list[ForumActorRef] = []

    def get_author_stats(self, actor: ForumActorRef):
        self.single_actors.append(actor)
        return ForumAuthorStatsRecord(
            topic_count=11, reply_count=12, accepted_reply_count=13
        )

    def get_author_stats_batch(self, actors):
        self.batch_actors = actors
        return {
            actor: ForumAuthorStatsRecord(
                topic_count=8 + index,
                reply_count=9 + index,
                accepted_reply_count=10 + index,
            )
            for index, actor in enumerate(actors)
        }


def _service(
    *,
    repository: FakeRepository | None = None,
    verifier: FakeVerifier | None = None,
    stats: FakeAuthorStatsService | None = None,
):
    return ForumInteractionService(
        repository or FakeRepository(),
        verifier or FakeVerifier(),
        stats or FakeAuthorStatsService(),
    )


def _request(
    *,
    action_type: str = "LIKE",
    target_type: str = "TOPIC",
    target_id: str = "topic-1",
    actor_type: str = "HUMAN",
    actor_id: str = "user-a",
    active: int = 1,
):
    return _service().record_interaction(
        actor_type=actor_type,
        actor_id=actor_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        active=active,
    )


@pytest.mark.parametrize(
    ("target_type", "target_id"),
    [("TOPIC", "topic-1"), ("POST", "post-1")],
)
@pytest.mark.parametrize("action_type", ["VIEW", "LIKE", "FAVORITE", "FOLLOW"])
def test_content_matrix_records_active_state(action_type, target_type, target_id):
    repo = FakeRepository()
    verifier = FakeVerifier()

    _service(repository=repo, verifier=verifier).record_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        active=1,
    )

    assert repo.inserted == [
        {
            "actor_type": "HUMAN",
            "actor_id": "user-a",
            "action_type": action_type,
            "target_type": target_type,
            "target_id": target_id,
            "active": 1,
        }
    ]
    expected_call = "topic" if target_type == "TOPIC" else "post"
    assert verifier.calls[0] == (expected_call, target_id)


@pytest.mark.parametrize("target_type", ["HUMAN", "BOT"])
def test_identity_target_only_accepts_follow(target_type):
    if target_type == "HUMAN":
        target_id = "user-b"
    else:
        target_id = "bot-a"
    repo = FakeRepository()

    _service(repository=repo).record_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="FOLLOW",
        target_type=target_type,
        target_id=target_id,
        active=1,
    )

    assert repo.inserted == [
        {
            "actor_type": "HUMAN",
            "actor_id": "user-a",
            "action_type": "FOLLOW",
            "target_type": target_type,
            "target_id": target_id,
            "active": 1,
        }
    ]


def test_active_state_is_validated_and_passed_to_persistence():
    repo = FakeRepository()
    with pytest.raises(ValidationError, match="active must be 1 or -1"):
        _request(active=0)
    with pytest.raises(ValidationError, match="active must be 1 or -1"):
        _request(active=True)
    with pytest.raises(ValidationError, match="active must be 1 or -1"):
        _request(active=False)

    record = _service(repository=repo).record_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
        active=-1,
    )

    assert record.active == -1
    assert repo.inserted == [
        {
            "actor_type": "HUMAN",
            "actor_id": "user-a",
            "action_type": "LIKE",
            "target_type": "TOPIC",
            "target_id": "topic-1",
            "active": -1,
        }
    ]


@pytest.mark.parametrize("action_type", ["VIEW", "LIKE", "FAVORITE", "ACCEPT"])
def test_identity_target_rejects_non_follow_actions(action_type):
    with pytest.raises(ValidationError):
        _request(action_type=action_type, target_type="HUMAN", target_id="user-b")


@pytest.mark.parametrize("target_type", ["TOPIC"])
def test_accept_rejects_non_post_targets(target_type):
    with pytest.raises(ValidationError):
        _request(action_type="ACCEPT", target_type=target_type)


def test_accept_requires_help_topic_and_first_level_post():
    repo = FakeRepository()
    verifier = FakeVerifier(topic=_topic(), post=_post())

    _service(repository=repo, verifier=verifier).record_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="ACCEPT",
        target_type="POST",
        target_id="post-1",
        active=1,
    )

    assert repo.inserted[0]["action_type"] == "ACCEPT"


@pytest.mark.parametrize(
    ("topic", "post", "match"),
    [
        (_topic(topic_type="DISCUSSION"), _post(), "HELP topics"),
        (_topic(), _post(reply_to_post_id="post-parent"), "first-level replies"),
    ],
)
def test_accept_rejects_invalid_post_or_topic(topic, post, match):
    verifier = FakeVerifier(topic=topic, post=post)
    with pytest.raises(ValidationError, match=match):
        _service(verifier=verifier).record_interaction(
            actor_type="HUMAN",
            actor_id="user-a",
            action_type="ACCEPT",
            target_type="POST",
            target_id="post-1",
            active=1,
        )


def test_accept_requires_topic_author_identity():
    verifier = FakeVerifier(topic=_topic(author_id="user-other"), post=_post())
    with pytest.raises(Forbidden, match="topic author"):
        _service(verifier=verifier).record_interaction(
            actor_type="HUMAN",
            actor_id="user-a",
            action_type="ACCEPT",
            target_type="POST",
            target_id="post-1",
            active=1,
        )


def test_missing_topic_and_post_targets_are_not_writable():
    verifier = FakeVerifier(topic=None, post=None)
    with pytest.raises(NotFound, match="topic target"):
        _service(verifier=verifier).record_interaction(
            actor_type="HUMAN",
            actor_id="user-a",
            action_type="VIEW",
            target_type="TOPIC",
            target_id="missing",
            active=1,
        )
    with pytest.raises(NotFound, match="post target"):
        _service(verifier=verifier).record_interaction(
            actor_type="HUMAN",
            actor_id="user-a",
            action_type="LIKE",
            target_type="POST",
            target_id="missing",
            active=1,
        )


def test_identity_verifier_failure_is_not_treated_as_valid_target():
    class FailingVerifier(FakeVerifier):
        def verify_human(self, human_id: str):
            raise RuntimeError("directory unavailable")

    with pytest.raises(RuntimeError, match="directory unavailable"):
        _service(verifier=FailingVerifier()).record_interaction(
            actor_type="HUMAN",
            actor_id="user-a",
            action_type="FOLLOW",
            target_type="HUMAN",
            target_id="user-b",
            active=1,
        )


def test_repository_failure_prevents_a_successful_state():
    class FailingRepository(FakeRepository):
        def insert_interaction(self, **kwargs):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        _service(repository=FailingRepository()).record_interaction(
            actor_type="HUMAN",
            actor_id="user-a",
            action_type="LIKE",
            target_type="TOPIC",
            target_id="topic-1",
            active=1,
        )


def test_summary_batch_validates_boundaries_and_preserves_order():
    service = _service(stats=FakeAuthorStatsService())
    actor = ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id="topic-1")
    actor_only = ForumInteractionSummaryQuery(
        actor_type=actor.type,
        actor_id=actor.id,
    )
    target_only = ForumInteractionSummaryQuery(
        target_type=target.type,
        target_id=target.id,
    )
    pair = ForumInteractionSummaryQuery(
        actor_type=actor.type,
        actor_id=actor.id,
        target_type=target.type,
        target_id=target.id,
    )

    results = service.get_summary_batch((target_only, actor_only, pair, target_only))

    assert [result.scope for result in results] == [
        ForumSummaryScope.TARGET,
        ForumSummaryScope.ACTOR,
        ForumSummaryScope.ACTOR_TARGET,
        ForumSummaryScope.TARGET,
    ]
    assert results[0].stats.reply_participant_count == 4
    assert results[1].stats.follow_count == 2
    assert results[1].stats.topic_count == 8
    assert results[1].stats.view_count == 0
    assert results[1].stats.like_count == 0
    assert results[1].stats.accept_count == 0
    assert results[2].stats.like_count == 7
    assert results[3] == results[0]


def test_summary_batch_rejects_empty_half_dimensions_and_length():
    service = _service()
    with pytest.raises(ValidationError, match="must not be empty"):
        service.get_summary_batch(())
    with pytest.raises(ValidationError, match="no more than 100"):
        service.get_summary_batch(
            tuple(
                ForumInteractionSummaryQuery(
                    target_type=ForumInteractionTargetType.TOPIC,
                    target_id="topic-1",
                )
                for _ in range(MAX_SUMMARY_BATCH_ITEMS + 1)
            )
        )
    with pytest.raises(ValidationError, match="actor_type and actor_id"):
        service.get_summary_batch((ForumInteractionSummaryQuery(actor_type="HUMAN"),))
    with pytest.raises(ValidationError, match="target_type and target_id"):
        service.get_summary_batch(
            (ForumInteractionSummaryQuery(target_type="TOPIC", target_id=None),)
        )
    with pytest.raises(ValidationError, match="must select"):
        service.get_summary_batch((ForumInteractionSummaryQuery(),))


def test_actor_dimension_counts_only_follow_events_and_author_production():
    stats = FakeAuthorStatsService()
    service = _service(stats=stats)
    query = ForumInteractionSummaryQuery(
        actor_type=ForumActorType.HUMAN, actor_id="user-a"
    )
    result = service.get_summary(query)

    assert result.scope is ForumSummaryScope.ACTOR
    assert result.stats.follow_count == 2
    assert (
        result.stats.topic_count,
        result.stats.reply_count,
        result.stats.accepted_reply_count,
    ) == (8, 9, 10)
    assert stats.single_actors == []
