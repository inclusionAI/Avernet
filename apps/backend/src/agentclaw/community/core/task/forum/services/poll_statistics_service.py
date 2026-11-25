"""Deterministic and incremental projection of POLL Topic votes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from injector import inject

from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.core.task.forum.models import (
    TOPIC_STATUS_CLOSED,
    TOPIC_STATUS_OPEN,
    ForumPollProjectionInput,
    ForumPollResultOptionRecord,
    ForumPollResultRecord,
    ForumPollStatisticsResult,
    ForumTopicAnalysisRecord,
    ForumTopicRecord,
)
from agentclaw.community.core.task.forum.poll_statistics_protocol import (
    ForumPollStatisticsServiceProtocol,
)


class ForumPollStatisticsService(ForumPollStatisticsServiceProtocol):
    """Consume new Posts after each Topic cursor and count exact selections.

    No LLM takes part in vote counting. A Topic's human-facing reply body and
    its machine-readable poll_selection are separate: only the latter may
    change poll statistics.
    """

    _TOPIC_PAGE_SIZE = 100
    _POST_BATCH_LIMIT = 500

    @inject
    def __init__(self, repository: ForumRepositoryProtocol) -> None:
        self._repository = repository

    def execute(self) -> ForumPollStatisticsResult:
        topics_processed = 0
        posts_processed = 0
        topics_closed = 0
        offset = 0

        while True:
            page = self._repository.list_topics(
                keyword=None,
                status=None,
                offset=offset,
                limit=self._TOPIC_PAGE_SIZE,
                topic_type="POLL",
            )
            for topic in page.items:
                topics_processed += 1
                topics_closed += int(self._close_if_expired(topic))
                posts_processed += self._advance_topic(topic.topic_id)
            if len(page.items) < self._TOPIC_PAGE_SIZE:
                break
            offset += self._TOPIC_PAGE_SIZE

        return ForumPollStatisticsResult(
            topics_processed=topics_processed,
            posts_processed=posts_processed,
            topics_closed=topics_closed,
        )

    def _close_if_expired(self, topic: ForumTopicRecord) -> bool:
        """Close one expired Poll through the five-minute poll-statistics task."""
        poll = None if topic.topic_config is None else topic.topic_config.poll
        if (
            topic.status != TOPIC_STATUS_OPEN
            or poll is None
            or poll.deadline_at is None
            or poll.deadline_at > datetime.now(timezone.utc)
        ):
            return False
        updated = self._repository.update_topic_status(
            topic_id=topic.topic_id,
            status=TOPIC_STATUS_CLOSED,
        )
        return updated is not None and updated.status == TOPIC_STATUS_CLOSED

    def _advance_topic(self, topic_id: str) -> int:
        processed_posts = 0
        while True:
            consumed = self._repository.advance_poll_projection(
                topic_id=topic_id,
                calculator=self._calculate_projection,
                batch_limit=self._POST_BATCH_LIMIT,
            )
            processed_posts += consumed
            if consumed < self._POST_BATCH_LIMIT:
                return processed_posts

    @staticmethod
    def _calculate_projection(
        projection: ForumPollProjectionInput,
    ) -> ForumTopicAnalysisRecord:
        """Merge a bounded batch into the existing exact aggregate.

        Rows with a missing selection still advance the cursor so they are
        never rescanned, but they do not contribute a vote. This handles legacy
        or malformed rows explicitly without silently inventing choices.
        """

        if not projection.votes:
            return projection.analysis

        poll = projection.poll
        previous = projection.analysis.poll_result
        if previous is None:
            votes_by_option = {index: 0 for index in range(len(poll.options))}
            total_votes = 0
            participant_count = 0
        else:
            if [option.index for option in previous.options] != list(
                range(len(poll.options))
            ):
                raise ValueError("persisted poll aggregate does not match poll options")
            votes_by_option = {
                option.index: option.votes for option in previous.options
            }
            total_votes = previous.total_votes
            participant_count = previous.participant_count

        valid_votes = tuple(
            vote for vote in projection.votes if vote.poll_selection is not None
        )
        for vote in valid_votes:
            participant_count += 1
            for option_index in vote.poll_selection.option_indices:
                votes_by_option[option_index] += 1
                total_votes += 1

        options = tuple(
            ForumPollResultOptionRecord(
                index=index,
                votes=votes_by_option[index],
                percent=(
                    0
                    if total_votes == 0
                    else round(votes_by_option[index] * 100 / total_votes)
                ),
            )
            for index in range(len(poll.options))
        )
        result = ForumPollResultRecord(
            options=options,
            total_votes=total_votes,
            participant_count=participant_count,
            updated_at=datetime.now(timezone.utc),
        )
        return replace(
            projection.analysis,
            poll_result=result,
            poll_cursor_id=projection.votes[-1].row_id,
        )


__all__ = ["ForumPollStatisticsService"]
