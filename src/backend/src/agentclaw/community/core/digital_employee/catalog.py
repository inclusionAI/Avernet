"""Platform catalog projection from Bot metadata and real capability sources."""
import asyncio
from typing import Any

from injector import inject

from agentclaw.community.core.digital_employee.capabilities import DigitalEmployeeCapabilityReader
from agentclaw.community.core.digital_employee.contracts import (
    DigitalEmployeeError, DigitalEmployeeRepositoryProtocol, DigitalEmployeeServiceProtocol,
    DigitalEmployeeSettings,
)
from agentclaw.community.core.engine_runtime.engine_runtime_service_protocol import EngineRuntimeRelayProtocol
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol


class DigitalEmployeeCatalogService:
    @inject
    def __init__(self, settings: DigitalEmployeeSettings, bots: DigitalEmployeeRepositoryProtocol,
                 binding: DigitalEmployeeServiceProtocol, capabilities: DigitalEmployeeCapabilityReader,
                 publications: BotPublishRepositoryProtocol, runtime: EngineRuntimeRelayProtocol, devices: DeviceBindingRepository) -> None:
        self._settings, self._bots, self._binding = settings, bots, binding
        self._capabilities, self._publications, self._runtime = capabilities, publications, runtime
        self._devices = devices

    async def detail(self, agent_id: int, version_status: str) -> dict[str, Any]:
        bot = await asyncio.to_thread(self._bots.get_bot, agent_id)
        if bot["bot_type"] != "service":
            raise DigitalEmployeeError("Only service Bots may bind digital employees")
        candidates = await asyncio.to_thread(self._binding.list_bindable, bot["creator_id"])
        summary = next((item for item in candidates if item["agentId"] == str(agent_id)), None)
        if summary is None:
            raise DigitalEmployeeError("服务 Bot 没有可运行实例")
        if version_status == "draft":
            snapshot = await asyncio.to_thread(self._capabilities.read, bot)
            stage = await asyncio.to_thread(self._runnable_stage, bot)
        elif version_status in {"published", "applying"}:
            records = await asyncio.to_thread(self._publications.list_by_source_bot, agent_id, bot["env"])
            published = [record for record in records if record.status == ("success" if version_status == "published" else "validating")]
            if not published:
                raise DigitalEmployeeError("服务 Bot 没有对应发布版本")
            record = max(published, key=lambda item: item.id)
            snapshot = await asyncio.to_thread(self._capabilities.read_published, bot, record)
            stage = "online" if version_status == "published" else "verify"
        else:
            raise DigitalEmployeeError("Unsupported versionStatus")
        models = await self._runtime.call(bot_id=bot["bot_id"], owner_id=bot["owner_id"],
                                          method="GET", path="/api/models", stage=stage)
        model_items = models.data.get("models") if isinstance(models.data, dict) else None
        if not isinstance(model_items, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in model_items):
            raise DigitalEmployeeError("无法读取 Bot 实际可用模型")
        caps = snapshot["capabilities"]
        return {**summary, "agentName": bot.get("bot_name") or bot["bot_id"], "agentCode": snapshot["agent_code"],
                "agentPlatform": self._settings.platform_code, "agentFramework": bot["active_engine"],
                "modelList": [item["id"] for item in model_items], "queriedVersionStatus": version_status,
                "mcpDetailList": [{"mcp_id": item["mcpServerCode"], "name": item.get("name") or item["mcpServerCode"],
                                   "description": item.get("description")} for item in caps["mcps"]],
                "skillList": [{"skill_id": item["skillId"], "name": item["name"],
                               "skill_source": snapshot["skill_sources"][item["skillId"]], "version": item["version"],
                               "bundleUrl": item["ossAddress"], "description": item.get("description"),
                               "owner": item.get("owner"), "modifier": item.get("modifier")} for item in caps["skills"]],
                "cliLis": [{"cliCode": item["cliCode"], "name": item.get("name") or item["cliCode"],
                            "description": item.get("description")} for item in caps["clis"]]}

    def _runnable_stage(self, bot: dict[str, Any]) -> str:
        if bot.get("binding_id"):
            binding = self._devices.get_by_id(bot["binding_id"])
            if binding is not None and binding.status == "ACTIVE":
                return "draft"
        records = self._publications.list_by_source_bot(bot["id"], bot["env"])
        for status, stage in (("success", "online"), ("validating", "verify")):
            for record in sorted(records, key=lambda item: item.id, reverse=True):
                if record.status != status:
                    continue
                binding_id = ((record.ext or {}).get("binding") or {}).get(stage)
                if binding_id:
                    binding = self._devices.get_by_id(binding_id)
                    if binding is not None and binding.status == "ACTIVE":
                        return stage
        raise DigitalEmployeeError("服务 Bot 没有可运行实例")
