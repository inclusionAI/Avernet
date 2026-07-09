"""Neutral device-provider lookup helpers.

Resolve a bot's device binding — ``(device_provider, sandbox_id)`` — from the
``BotRepository``. Provider-agnostic (the value is just whatever the binding
stored: ``arca`` / ``baas`` / ``teclaw`` / ``None``); no vendor logic lives here.
Moved out of ``utils/arca_utils`` (B6) so the upper layers that only need the
binding lookup don't import a vendor module.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger

logger = get_logger()


def get_device_info(bot_id: str, owner_id: str, bot_repo) -> tuple[str | None, str | None]:
    """Resolve ``(device_provider, sandbox_id)`` for ``bot_id`` + ``owner_id``.

    Args:
        bot_id: Bot ID.
        owner_id: Owner user ID.
        bot_repo: ``BotRepository`` instance (DI-supplied by caller).

    Returns:
        ``(device_provider, sandbox_id)`` — e.g. ``("arca", "xxx")`` or
        ``("baas", None)``. Returns ``(None, None)`` on invalid args or lookup
        failure.
    """
    if not bot_id or not owner_id:
        return None, None

    try:
        device_info = bot_repo.get_device_provider_by_bot_id_and_owner(bot_id, owner_id)
        if device_info:
            return device_info.get("device_provider"), device_info.get("sandbox_id")
        return None, None
    except Exception as e:
        logger.error(f"[device_info.get_device_info] Failed to query device for bot {bot_id}: {e}")
        return None, None


def get_device_info_by_bot_id(bot_id: str, bot_repo) -> tuple[str | None, str | None]:
    """Resolve ``(device_provider, sandbox_id)`` for ``bot_id`` (no owner).

    Args:
        bot_id: Bot ID.
        bot_repo: ``BotRepository`` instance (DI-supplied by caller).

    Returns:
        ``(device_provider, sandbox_id)``, or ``(None, None)`` on invalid args
        or lookup failure.
    """
    if not bot_id:
        return None, None

    try:
        device_info = bot_repo.get_device_provider_by_bot_id(bot_id)
        if device_info:
            return device_info.get("device_provider"), device_info.get("sandbox_id")
        return None, None
    except Exception as e:
        logger.error(f"[device_info.get_device_info_by_bot_id] Failed to query device for bot {bot_id}: {e}")
        return None, None
