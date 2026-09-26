"""BotService._require_workspace_hosting 私有守卫仍由 create 期使用，单测保留。"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_service():
    """Construct BotService with mocked deps, bypassing @inject."""
    from agentclaw.community.core.bot_management.services.bot_service import BotService

    svc = BotService.__new__(BotService)
    svc._workspace_hosting_service = MagicMock()
    return svc


def test_require_workspace_hosting_returns_service_when_configured():
    from agentclaw.community.core.bot_management.services.bot_service import BotService

    svc = _make_service()
    sentinel = svc._workspace_hosting_service
    assert BotService._require_workspace_hosting(svc) is sentinel
