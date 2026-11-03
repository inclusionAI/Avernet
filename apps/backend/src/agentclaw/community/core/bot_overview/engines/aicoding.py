"""Bot overview provider for AICoding / Claude Code Bots.

Draft: returns only the (empty) engine payload. The engine owner fills the
neutral fields (``default_model``, ``skills``, ``mcps``, ``daily_sessions``) and adds
engine-specific fields to :class:`AICodingEnginePayload`.
"""

from __future__ import annotations

from ..models import EngineBotOverview
from ..payloads import AICodingEnginePayload
from ..provider import BotOverviewQuery


class AICodingBotOverviewProvider:
    """Serves both ``aicoding`` and ``claude_code`` Bots."""

    def collect(self, query: BotOverviewQuery) -> EngineBotOverview:
        # TODO(aicoding engine owner): report default_model, skills, mcps and
        # session activity since ``query.activity_since`` from the engine.
        return EngineBotOverview(payload=AICodingEnginePayload(engine=query.engine))


__all__ = ["AICodingBotOverviewProvider"]
