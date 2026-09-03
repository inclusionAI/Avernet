"""BCS Bot 身份解析：产品 Bot ID → BCS ``bot_id:owner_id`` UUID。"""
from __future__ import annotations

from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_runner.client.ports import BcsBotIdentityResolver
from agentclaw.community.core.task.task_runner.client.protocols import BotServiceProtocol


class BotServiceBcsBotIdentityResolver(BcsBotIdentityResolver):
    """基于 BotService 权威记录解析 BCS 身份；不信任 LLM/搜推结果携带 owner。"""

    def __init__(self, bot_service: BotServiceProtocol) -> None:
        self._bot_service = bot_service

    def resolve_many(self, product_bot_ids: list[str]) -> dict[str, str]:
        """产品 Bot ID → BCS ``bot_id:owner_id`` 身份。

        已带 ``:``(如上游已 resolve 成 BCS ``bot_id:owner_id``)的视为身份完整,原样透传、不再查 BotService,
        也只把真正需要查询的无 ``:`` id 经 BotService 取 owner 后拼接。"""
        ids = list(dict.fromkeys(str(bot_id).strip() for bot_id in product_bot_ids if str(bot_id).strip()))
        if not ids:
            raise BotIdentityResolutionError("BCS identity resolution requires at least one bot_id")
        # 已带 ':'(BCS 'bot_id:owner_id')原样透传,不再查 BotService 重复拼接/查询。
        resolved: dict[str, str] = {bot_id: bot_id for bot_id in ids if ":" in bot_id}
        need_lookup = [bot_id for bot_id in ids if ":" not in bot_id]
        if not need_lookup:
            return resolved
        page = self._bot_service.list_bots_by_conditions(
            bot_ids=need_lookup,
            page=1,
            page_size=len(need_lookup),
        )
        items = (page or {}).get("items") or []
        by_id: dict[str, list[dict]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            bot_id = str(item.get("bot_id") or "").strip()
            if bot_id in need_lookup:
                by_id.setdefault(bot_id, []).append(item)

        for bot_id in need_lookup:
            matches = by_id.get(bot_id) or []
            if not matches:
                raise BotIdentityResolutionError(f"bot not found for BCS identity: {bot_id}")
            if len(matches) != 1:
                raise BotIdentityResolutionError(
                    f"ambiguous bot owner for BCS identity: bot_id={bot_id}, matches={len(matches)}"
                )
            owner_id = str(matches[0].get("owner_id") or "").strip()
            if not owner_id:
                raise BotIdentityResolutionError(f"bot owner_id missing: {bot_id}")
            resolved[bot_id] = f"{bot_id}:{owner_id}"
        return resolved
