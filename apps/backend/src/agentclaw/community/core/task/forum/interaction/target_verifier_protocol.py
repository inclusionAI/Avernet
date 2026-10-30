"""Provider contract for BBS interaction target existence checks."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.forum.models import (
    ForumPostRecord,
    ForumTopicRecord,
)


@runtime_checkable
class ForumInteractionTargetVerifierProtocol(Protocol):
    @abstractmethod
    def verify_topic(self, topic_id: str) -> ForumTopicRecord | None:
        """Return the visible Topic, or ``None`` when it cannot be targeted."""

    @abstractmethod
    def verify_post(self, post_id: str) -> ForumPostRecord | None:
        """Return a visible Topic Post, or ``None`` when it cannot be targeted."""

    @abstractmethod
    def verify_bot(self, bot_id: str) -> bool:
        """Return whether the stable BOT identity exists."""
