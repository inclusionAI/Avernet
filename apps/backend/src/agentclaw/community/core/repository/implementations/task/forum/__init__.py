"""Forum repository implementations."""

from .forum_digest_repository import ForumDigestReportRepository
from .forum_repository import ForumRepository
from .forum_author_stats_repository import ForumAuthorStatsRepository
from .forum_interaction_repository import ForumInteractionRepository
from .personal_center_activity_repository import ForumPersonalCenterActivityRepository
from .forum_topic_tag_repository import ForumTopicTagRepository

__all__ = [
    "ForumAuthorStatsRepository",
    "ForumDigestReportRepository",
    "ForumInteractionRepository",
    "ForumPersonalCenterActivityRepository",
    "ForumRepository",
    "ForumTopicTagRepository",
]
