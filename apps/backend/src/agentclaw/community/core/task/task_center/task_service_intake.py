"""TaskService facade methods for clarification state, separate from execute."""

from __future__ import annotations

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.intake import (
    TaskIntakeCommand, TaskIntakeContext, TaskIntakeResult,
)


class TaskServiceIntakeMixin:
    def get_confirmed_intake(
        self, session_id: str, owner_user_id: str,
    ) -> TaskIntakeResult:
        if self._intake_handler is None:
            raise TaskStateError("task intake is not configured")
        return self._intake_handler.get_confirmed(session_id, owner_user_id)

    async def intake(
        self, command: TaskIntakeCommand, context: TaskIntakeContext,
    ) -> TaskIntakeResult:
        if self._intake_handler is None:
            raise TaskStateError("task intake is not configured")
        return await self._intake_handler.intake(command, context)
