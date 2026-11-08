"""Repository contract for updating Topic-derived tags."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.forum.models import ForumTopicRecord


@runtime_checkable
class ForumTopicTagRepositoryProtocol(Protocol):
    @abstractmethod
    def update_topic_tags(
        self,
        *,
        topic_id: str,
        tags: tuple[str, ...],
    ) -> Optional[ForumTopicRecord]:
        """Replace one Topic's derived tags while preserving other analysis.

        The write must not infer tags from text. Callers derive tags first and
        pass a closed structured tuple; persistence only stores the projection.
        """
