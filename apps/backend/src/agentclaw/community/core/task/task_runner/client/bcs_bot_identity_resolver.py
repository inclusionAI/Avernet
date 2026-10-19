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
        for bot_id in ids:
            product_id, separator, owner_id = bot_id.partition(":")
            if separator and (
                not product_id
                or not owner_id
                or ":" in owner_id
            ):
                raise BotIdentityResolutionError(
                    f"invalid full BCS bot identity: {bot_id}"
                )
        resolved: dict[str, str] = {bot_id: bot_id for bot_id in ids if ":" in bot_id}
        need_lookup = [bot_id for bot_id in ids if ":" not in bot_id]
        if not need_lookup:
            return resolved
        # A product bot_id can exist for many owners (notably each owner's
        # ``default`` bot). A page one row larger than the ID list makes at least
        # one duplicate visible; ``total`` is authoritative when the full result
        # still cannot fit on this page.
        page = self._bot_service.list_bots_by_conditions(
            bot_ids=need_lookup,
            page=1,
            page_size=max(len(need_lookup) + 1, 2),
        )
        items = (page or {}).get("items") or []
        by_id: dict[str, list[dict]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            bot_id = str(item.get("bot_id") or "").strip()
            if bot_id in need_lookup:
                by_id.setdefault(bot_id, []).append(item)

        total_raw = (page or {}).get("total")
        try:
            total = int(total_raw) if total_raw is not None else len(items)
        except (TypeError, ValueError):
            total = len(items)
        missing = [
            bot_id for bot_id in need_lookup if not by_id.get(bot_id)
        ]
        ambiguous = [
            bot_id for bot_id in need_lookup if len(by_id.get(bot_id) or []) > 1
        ]
        # ``total > len(need_lookup)`` means at least one product bot_id has
        # multiple owner rows even when a truncated page hides that duplicate.
        if ambiguous or total > len(need_lookup):
            raise BotIdentityResolutionError(
                "ambiguous bot owner for BCS identity: "
                f"bot_ids={ambiguous or need_lookup}, "
                f"total={max(total, len(items))}, returned={len(items)}"
            )
        if missing:
            raise BotIdentityResolutionError(
                "bot not found for BCS identity: "
                f"bot_ids={missing}, total={total}, returned={len(items)}"
            )

        for bot_id in need_lookup:
            matches = by_id.get(bot_id) or []
            owner_id = str(matches[0].get("owner_id") or "").strip()
            if not owner_id:
                raise BotIdentityResolutionError(f"bot owner_id missing: bot_id={bot_id}")
            resolved[bot_id] = f"{bot_id}:{owner_id}"
        return resolved
