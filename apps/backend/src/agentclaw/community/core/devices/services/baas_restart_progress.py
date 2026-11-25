"""Allowlisted progress telemetry for the existing BaaS restart poll task."""

from __future__ import annotations

import re
from typing import Any, Callable

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.log import get_logger

logger = get_logger()


def _field(value: Any, default: str = "-") -> str:
    if not isinstance(value, (str, int)):
        return default
    return re.sub(r"[^\w.:@/+-]", "_", str(value))[:256] or default


def log_restart_progress(
    *, payload: dict, binding: DeviceBindingRecord, bot_repository: Any,
    clock: Callable[[], float], timeout_s: int, status: str,
    publish_id: int | None = None,
    bot: Any = None,
) -> None:
    # Normal polling reuses the Bot already loaded by the handler. Adoption has
    # no Bot read of its own; a metadata-only failure must not break recovery.
    if bot is None and bot_repository is not None:
        try:
            bot = bot_repository.get_by_binding_id(binding.id)
        except Exception:
            bot = None
    engine = bot.get("active_engine") if isinstance(bot, dict) else None
    logger.info(
        "event=bot_restart_progress phase=publish_poll entry_point=baas.restart.publish_poll "
        "bot_id=%s binding_id=%s publish_id=%s request_id=%s "
        "engine_type=%s env=%s status=%s elapsed_s=%.3f timeout_s=%s",
        _field(payload["bot_id"]), binding.id, _field(publish_id),
        _field(payload.get("request_id")), _field(engine, "unknown"),
        _field(binding.env), _field(status),
        max(0.0, clock() - payload["started_at_epoch_s"]), timeout_s,
    )
