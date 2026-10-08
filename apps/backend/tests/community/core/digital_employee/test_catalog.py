from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agentclaw.community.core.digital_employee.catalog import DigitalEmployeeCatalogService
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeSettings, DigitalEmployeeError


@pytest.fixture
def s():
    bots, binding, capabilities, publications, runtime, devices = [Mock() for _ in range(6)]
    bot = {"id": 17, "env": "dev", "bot_type": "service", "creator_id": "owner", "bot_id": "bot", "owner_id": "owner", "active_engine": "openclaw", "binding_id": "draft-binding"}
    bots.get_bot.return_value = bot
    binding.list_bindable.return_value = [{"agentId": "17", "platform": "platform", "creatorNo": "owner"}]
    snapshot = {"agent_code": "agent", "skill_sources": {}, "capabilities": {"skills": [], "mcps": [], "clis": []}}
    capabilities.read.return_value = snapshot
    capabilities.read_published.return_value = snapshot
    devices.get_by_id.return_value = SimpleNamespace(status="ACTIVE")
    runtime.call = AsyncMock(return_value=SimpleNamespace(data={"models": [{"id": "provider/model"}]}))
    service = DigitalEmployeeCatalogService(DigitalEmployeeSettings(True, "platform"), bots, binding, capabilities, publications, runtime, devices)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_draft_catalog_uses_real_model_and_capabilities(s):
    result = await s.service.detail(17, "draft")
    assert result["modelList"] == ["provider/model"]
    assert s.runtime.call.call_args.kwargs["stage"] == "draft"
    s.capabilities.read.assert_called_once_with(s.bot)


@pytest.mark.asyncio
async def test_online_only_bot_can_expose_draft_configuration(s):
    s.bot["binding_id"] = None
    s.publications.list_by_source_bot.return_value = [SimpleNamespace(id=42, status="success", ext={"binding": {"online": "online-binding"}})]
    await s.service.detail(17, "draft")
    assert s.runtime.call.call_args.kwargs["stage"] == "online"


@pytest.mark.asyncio
async def test_published_catalog_never_reads_current_draft_skills(s):
    record = SimpleNamespace(id=42, status="success")
    s.publications.list_by_source_bot.return_value = [record]
    result = await s.service.detail(17, "published")
    assert result["queriedVersionStatus"] == "published"
    s.capabilities.read_published.assert_called_once_with(s.bot, record)
    s.capabilities.read.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_model_payload_is_not_a_fake_empty_list(s):
    s.runtime.call.return_value = SimpleNamespace(data={"error": "unavailable"})
    with pytest.raises(DigitalEmployeeError, match="模型"):
        await s.service.detail(17, "draft")
