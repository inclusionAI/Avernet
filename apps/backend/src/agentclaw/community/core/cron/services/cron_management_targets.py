"""Discover Cron targets without provisioning runtimes or reading engine jobs."""
from __future__ import annotations

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget, MULTI_INSTANCE_DEVICE_PROVIDERS,
)
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest


class CronManagementTargetsMixin:
    async def get_management_targets(
        self, *, bot_id: str, owner_id: str, actor_id: str,
    ) -> dict:
        """Return authoritative capabilities, explicit management permission and targets.

        An unavailable stage is returned as unavailable rather than silently falling
        back to another stage. This endpoint does not promise offline job storage.
        """
        bot = self._bot_provider.get_bot(bot_id, owner_id)
        engine = str(bot.get("active_engine") or bot.get("engine") or "").lower()
        service = bot.get("bot_type") == "service"
        caller = service and bot.get("call_type") == "caller"
        level = self._collaborators.get_operable_permission_level(bot=bot, user_id=actor_id)
        result = {
            "bot_id": bot_id, "bot_name": bot.get("bot_name", bot_id),
            "owner_id": bot.get("owner_id") or owner_id,
            "bot_type": "service" if service else "personal", "engine": engine,
            "caller_mode": caller, "supported": engine not in {"", "hermes"} and (bot.get("bot_type") != "desktop" or engine == "openclaw"),
            "can_manage": level >= PermissionLevel.ADMIN, "environments": [],
        }
        for stage in (("draft", "verify", "online") if service else ("draft",)):
            needs_instance = service and not (engine == "teclaw" and stage == "online")
            item = {"runtime_stage": stage, "available": False,
                    "requires_instance": needs_instance, "instances": []}
            if caller and stage != "online":
                item["reason"] = "Caller 模式仅支持生产态"
                result["environments"].append(item)
                continue
            try:
                if caller:
                    resolved = self._runtime_bindings.resolve(RuntimeBindingRequest(
                        bot_id=bot_id, owner_id=result["owner_id"],
                        actor_user_id=actor_id, stage=stage,
                    ))
                    target = CronRuntimeTarget(
                        bot_id=bot_id, bot_name=result["bot_name"], owner_id=result["owner_id"],
                        bot_type="service", runtime_stage=stage, binding_id=resolved.binding_id,
                        fixed_binding=True, caller_binding=True, operator_id=actor_id,
                        authenticated_user_id=actor_id,
                    )
                else:
                    _, target = self._resolve_request_target(
                        bot_id=bot_id, user_id=owner_id, runtime_stage=stage,
                    )
                device = self._get_active_runtime_device(bot_id=bot_id, runtime_stage=stage, target=target)
                if needs_instance:
                    provider = str(self._read_field(device, "device_provider") or "").lower()
                    if provider in MULTI_INSTANCE_DEVICE_PROVIDERS:
                        item["instances"] = await self._get_runtime_devices(target)
                    else:
                        uid = self._read_field(device, "device_id")
                        item["instances"] = [str(uid)] if uid else []
                    if not item["instances"]:
                        raise ValueError("当前环境暂无运行实例")
                item["available"] = True
            except Exception:
                # Do not expose connection URLs, provider errors or credentials.
                item["reason"] = "运行环境未就绪或实例暂不可用，请刷新后重试"
            result["environments"].append(item)
        return result
