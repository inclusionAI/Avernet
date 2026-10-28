"""Transport-agnostic Service API contract for personal-center activities."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.forum.interaction.models import (
    PersonalCenterActivityPage,
    PersonalCenterActivityQuery,
)


@runtime_checkable
class ForumPersonalCenterActivityServiceProtocol(Protocol):
    @abstractmethod
    def get_personal_center_activities(
        self, query: PersonalCenterActivityQuery
    ) -> PersonalCenterActivityPage:
        """Return a server-paged activity list for one authenticated owner."""
