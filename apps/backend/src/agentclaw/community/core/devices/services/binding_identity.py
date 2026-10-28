"""Resolve a binding's Bot without treating its immutable entity as an Owner."""

from typing import Any

from agentclaw.community.core.devices.protocols import BotQueryProtocol
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.errors import InvalidDeviceStatusError


def bot_for_binding(query: BotQueryProtocol, binding: DeviceBindingRecord) -> dict[str, Any] | None:
    """Draft bindings point to the Bot row; published bindings carry its entity.

    Authorization must use the returned current Owner. Only genuinely unlinked
    historical bindings retain their standalone entity-based ownership rule.
    """
    try:
        bot = query.get_by_binding_id(binding.id)
    except Exception as exc:
        raise InvalidDeviceStatusError("Bot 信息查询失败，无法校验权限") from exc
    if bot is not None:
        return bot
    bot_id = (binding.device_props or {}).get("bolt_id")
    if not bot_id:
        return None
    return query.get_by_id_and_entity(bot_id, binding.entity_id)
