"""Service API Protocol for Skills Pool operational evidence."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.operational_query import (
    BatchOperationalReport,
    BotOperationalView,
)


@runtime_checkable
class SkillsPoolOperationalQueryServiceProtocol(Protocol):
    def get_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
    ) -> BotOperationalView: ...

    def summarize_batch(
        self,
        *,
        env: str,
        engine: str,
        batch_id: str,
    ) -> BatchOperationalReport: ...
