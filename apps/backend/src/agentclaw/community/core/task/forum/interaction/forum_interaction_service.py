"""Application service for BBS interaction state and summaries."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.errors import (
    Forbidden,
    NotFound,
    ValidationError,
)
from agentclaw.community.core.repository.protocols.task.forum_interaction import (
    ForumInteractionRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    INTERACTION_TARGET_MATRIX,
    ForumActorRef,
    ForumActorTargetRef,
    ForumActorType,
    ForumAuthorStatsRecord,
    ForumInteractionActionType,
    ForumInteractionRecord,
    ForumInteractionSummaryQuery,
    ForumInteractionSummaryResult,
    ForumInteractionTargetType,
    ForumStatsRecord,
    ForumSummaryScope,
    ForumTargetRef,
    MAX_SUMMARY_BATCH_ITEMS,
    MAX_TARGET_ID_LENGTH,
)
from agentclaw.community.core.task.forum.interaction.service_protocol import (
    ForumAuthorStatsServiceProtocol,
    ForumInteractionServiceProtocol,
)
from agentclaw.community.core.task.forum.interaction.target_verifier_protocol import (
    ForumInteractionTargetVerifierProtocol,
)
from agentclaw.community.core.task.forum.models import (
    AUTHOR_TYPES,
    MAX_AUTHOR_ID_LENGTH,
    TOPIC_TYPE_HELP,
)


class ForumInteractionService(ForumInteractionServiceProtocol):
    """Validate interaction semantics, then delegate persistence and aggregates."""

    @inject
    def __init__(
        self,
        repository: ForumInteractionRepositoryProtocol,
        target_verifier: ForumInteractionTargetVerifierProtocol,
        author_stats_service: ForumAuthorStatsServiceProtocol,
    ) -> None:
        self._repository = repository
        self._target_verifier = target_verifier
        self._author_stats_service = author_stats_service

    def record_interaction(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
        active: int,
    ) -> ForumInteractionRecord:
        actor = self._normalize_actor(actor_type, actor_id)
        action_delta = self._normalize_active(active)
        action = self._normalize_action(action_type)
        target = self._normalize_target(target_type, target_id)
        if target.type not in INTERACTION_TARGET_MATRIX[action]:
            raise ValidationError(f"{action} is not allowed for target {target.type}")

        topic = None
        post = None
        if target.type is ForumInteractionTargetType.TOPIC:
            topic = self._target_verifier.verify_topic(target.id)
            if topic is None:
                raise NotFound("topic target not found")
        elif target.type is ForumInteractionTargetType.POST:
            post = self._target_verifier.verify_post(target.id)
            if post is None:
                raise NotFound("post target not found")
        elif target.type is ForumInteractionTargetType.HUMAN:
            if not self._target_verifier.verify_human(target.id):
                raise NotFound("human target not found")
        else:
            if not self._target_verifier.verify_bot(target.id):
                raise NotFound("bot target not found")

        if action is ForumInteractionActionType.ACCEPT:
            assert post is not None
            topic = self._target_verifier.verify_topic(post.topic_id)
            if topic is None:
                raise NotFound("topic target not found")
            if topic.topic_type != TOPIC_TYPE_HELP:
                raise ValidationError("ACCEPT is only allowed for HELP topics")
            if post.reply_to_post_id is not None:
                raise ValidationError("only first-level replies can be accepted")
            if topic.author_type != actor.type.value or topic.author_id != actor.id:
                raise Forbidden("only the topic author may accept replies")

        return self._repository.insert_interaction(
            actor_type=actor.type.value,
            actor_id=actor.id,
            action_type=action.value,
            target_type=target.type.value,
            target_id=target.id,
            active=action_delta,
        )

    def get_summary(
        self, query: ForumInteractionSummaryQuery
    ) -> ForumInteractionSummaryResult:
        return self.get_summary_batch((query,))[0]

    def get_summary_batch(
        self, items: tuple[ForumInteractionSummaryQuery, ...]
    ) -> tuple[ForumInteractionSummaryResult, ...]:
        if not items:
            raise ValidationError("items must not be empty")
        if len(items) > MAX_SUMMARY_BATCH_ITEMS:
            raise ValidationError(
                f"items must contain no more than {MAX_SUMMARY_BATCH_ITEMS} entries"
            )

        validated: list[
            tuple[ForumSummaryScope, ForumActorRef | None, ForumTargetRef | None]
        ] = []
        for index, query in enumerate(items):
            actor = self._validate_summary_actor(query)
            target = self._validate_summary_target(query)
            if actor is None and target is None:
                raise ValidationError(
                    f"items[{index}] must select actor, target, or actor and target"
                )
            scope = (
                ForumSummaryScope.ACTOR_TARGET
                if actor is not None and target is not None
                else ForumSummaryScope.ACTOR
                if actor is not None
                else ForumSummaryScope.TARGET
            )
            validated.append((scope, actor, target))

        actors = tuple(
            dict.fromkeys(item[1] for item in validated if item[1] is not None)
        )
        targets = tuple(
            dict.fromkeys(item[2] for item in validated if item[2] is not None)
        )
        pairs = tuple(
            dict.fromkeys(
                ForumActorTargetRef(actor=actor, target=target)
                for _, actor, target in validated
                if actor is not None and target is not None
            )
        )
        actor_event_stats = self._repository.batch_get_actor_summaries(actors)
        target_stats = self._repository.batch_get_target_summaries(targets)
        pair_stats = self._repository.batch_get_actor_target_summaries(pairs)
        author_stats: dict[ForumActorRef, ForumAuthorStatsRecord] = {}
        if actors:
            author_stats = self._author_stats_service.get_author_stats_batch(actors)

        results: list[ForumInteractionSummaryResult] = []
        for scope, actor, target in validated:
            stats = ForumStatsRecord()
            if scope is ForumSummaryScope.ACTOR:
                assert actor is not None
                events = actor_event_stats[actor]
                author = author_stats[actor]
                stats = ForumStatsRecord(
                    follow_count=events.follow_count,
                    topic_count=author.topic_count,
                    reply_count=author.reply_count,
                    accepted_reply_count=author.accepted_reply_count,
                )
            elif scope is ForumSummaryScope.TARGET:
                assert target is not None
                stats = target_stats[target]
            else:
                assert actor is not None and target is not None
                stats = pair_stats[ForumActorTargetRef(actor=actor, target=target)]
            results.append(
                ForumInteractionSummaryResult(
                    scope=scope,
                    actor_type=actor.type if actor is not None else None,
                    actor_id=actor.id if actor is not None else None,
                    target_type=target.type if target is not None else None,
                    target_id=target.id if target is not None else None,
                    stats=stats,
                )
            )
        return tuple(results)

    def _validate_summary_actor(
        self, query: ForumInteractionSummaryQuery
    ) -> ForumActorRef | None:
        if (query.actor_type is None) != (query.actor_id is None):
            raise ValidationError("actor_type and actor_id must be provided together")
        if query.actor_type is None or query.actor_id is None:
            return None
        return self._normalize_actor(query.actor_type, query.actor_id)

    def _validate_summary_target(
        self, query: ForumInteractionSummaryQuery
    ) -> ForumTargetRef | None:
        if (query.target_type is None) != (query.target_id is None):
            raise ValidationError("target_type and target_id must be provided together")
        if query.target_type is None or query.target_id is None:
            return None
        return self._normalize_target(query.target_type, query.target_id)

    @staticmethod
    def _normalize_active(value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value not in (1, -1):
            raise ValidationError("active must be 1 or -1")
        return value

    @staticmethod
    def _required_text(value: str, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(
                f"{field} must contain no more than {max_length} characters"
            )
        return normalized

    def _normalize_actor(self, actor_type: str, actor_id: str) -> ForumActorRef:
        normalized_type = self._required_text(actor_type, "actor_type", 16).upper()
        if normalized_type not in AUTHOR_TYPES:
            allowed = ", ".join(sorted(AUTHOR_TYPES))
            raise ValidationError(f"actor_type must be one of: {allowed}")
        normalized_id = self._required_text(actor_id, "actor_id", MAX_AUTHOR_ID_LENGTH)
        return ForumActorRef(type=ForumActorType(normalized_type), id=normalized_id)

    def _normalize_action(self, value: str) -> ForumInteractionActionType:
        normalized = self._required_text(value, "action_type", 16).upper()
        try:
            return ForumInteractionActionType(normalized)
        except ValueError:
            allowed = ", ".join(item.value for item in ForumInteractionActionType)
            raise ValidationError(f"action_type must be one of: {allowed}") from None

    def _normalize_target(self, target_type: str, target_id: str) -> ForumTargetRef:
        normalized_type = self._required_text(target_type, "target_type", 16).upper()
        try:
            normalized_target_type = ForumInteractionTargetType(normalized_type)
        except ValueError:
            allowed = ", ".join(item.value for item in ForumInteractionTargetType)
            raise ValidationError(f"target_type must be one of: {allowed}") from None
        normalized_id = self._required_text(
            target_id, "target_id", MAX_TARGET_ID_LENGTH
        )
        return ForumTargetRef(type=normalized_target_type, id=normalized_id)
