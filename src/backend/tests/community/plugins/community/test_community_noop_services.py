"""Community no-op services (B9): workflow-catalog / AntCode / bot-chat /
device-connection-manager + the all-baas create rollout policy.

These ship in the community distribution and back real routes, so they must be
exercised directly (the resolution guard only proves they're bound).
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_chat.schemas import ConversationDetail, HealthCheckData
from agentclaw.community.core.devices.services.device_service import BAAS_DEVICE_PROVIDER
from agentclaw.community.plugins.community.aicoding import NoopWorkflowCatalogService
from agentclaw.community.plugins.community.app_services import (
    NoopCodePlatformService,
    NoopBotChatService,
)
from agentclaw.community.plugins.community.device_connection_manager import (
    CommunityDeviceConnectionManager,
)
from agentclaw.community.plugins.community.devices import CommunityAllBaasRolloutPolicy


@pytest.mark.asyncio
async def test_workflow_catalog_is_empty():
    svc = NoopWorkflowCatalogService()
    assert await svc.list_workflows() == []
    assert await svc.list_workflows(branch="release") == []


def test_antcode_noop():
    svc = NoopCodePlatformService()
    assert svc.get_private_token() is None
    assert svc.get_private_token(cookie="c=1") is None
    assert svc.search_user_projects("q", page=1) == []


@pytest.mark.asyncio
async def test_bot_chat_noop_neutral_responses():
    svc = NoopBotChatService()
    assert await svc.list_sessions("owner-1") == []

    detail = await svc.get_session("trace-9", owner_id="owner-1")
    assert isinstance(detail, ConversationDetail)
    assert detail.id == "trace-9"

    health = await svc.health_check()
    assert isinstance(health, HealthCheckData)
    assert health.status == "healthy"


@pytest.mark.asyncio
async def test_community_connection_manager_is_noop():
    m = CommunityDeviceConnectionManager()
    assert await m.get_device_ip("e", "staff", "b") is None
    assert await m.get_connection("e", "staff", "b") is None
    assert await m.get_client("e", "staff", "b") is None
    assert await m.close_client("e", "staff", "b") is None
    assert await m.close_all() is None


def test_all_baas_rollout_policy_always_decides_baas():
    policy = CommunityAllBaasRolloutPolicy()
    for bot_type, engine in [
        ("personal", "openclaw"),
        ("service", "claude_code"),
        ("desktop", ""),
        ("anything", "xyz"),
    ]:
        decision = policy.decide(
            user_id="staff-1",
            bot_type=bot_type,
            engine_type=engine,
            template_type="",
        )
        assert decision.target_provider == BAAS_DEVICE_PROVIDER
