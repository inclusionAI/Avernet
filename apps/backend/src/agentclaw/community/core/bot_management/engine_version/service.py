"""Inspect one draft runtime without changing its lifecycle or image policy."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from collections.abc import Callable
from typing import Any
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.engine_runtime.gate import require_bot_operator
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)

from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
)
from .errors import (
    EngineVersionInstanceSelectionError,
    EngineVersionStageUnsupportedError,
)
from .models import (
    CurrentVersion,
    Detection,
    EngineVersionConfig,
    EngineVersionResult,
    VersionContext,
    EngineReleaseProbe,
)
from .release import compare, release_target


from .protocol import EngineVersionServiceProtocol


class EngineVersionService(EngineVersionServiceProtocol):
    def __init__(
        self,
        *,
        bots: BotRepository,
        bindings: DeviceBindingRepository,
        resolver: DeviceContextResolver,
        probe: EngineReleaseProbe,
        collaborators: CollaboratorServiceProtocol,
        list_devices: Callable[[str], list[dict[str, Any]]],
        config: EngineVersionConfig,
    ) -> None:
        self._bots = bots
        self._bindings = bindings
        self._resolver = resolver
        self._probe = probe
        self._collaborators = collaborators
        self._list_devices = list_devices
        self._config = config

    async def inspect(
        self,
        *,
        bot_id: str,
        owner_id: str,
        caller_id: str,
        stage: str = "draft",
        device_uuid: str | None = None,
    ) -> EngineVersionResult:
        bot = await asyncio.to_thread(self._bots.get_by_id_and_owner, bot_id, owner_id)
        if not bot:
            raise BotNotFoundError("Bot not found")
        await asyncio.to_thread(
            require_bot_operator,
            self._collaborators,
            bot=bot,
            bot_id=bot_id,
            owner_id=owner_id,
            caller_id=caller_id,
        )
        if stage != "draft":
            raise EngineVersionStageUnsupportedError("stage_not_supported")
        if bot.get("bot_type") not in {"personal", "service"}:
            raise EngineBotTypeNotSupportedError("Bot type not supported")
        context: VersionContext = {
            "bot_id": bot_id,
            "stage": stage,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        target = release_target(self._config.target_release)
        if bot.get("active_engine") not in {"openclaw", "hermes"}:
            return {
                "detection": {
                    "status": "not_applicable",
                    "reason": "engine_not_supported",
                },
                "release_target": target,
                "comparison": "unknown",
                "restart_hint": "none",
                "context": context,
            }
        detection: Detection = {"status": "read_failed", "reason": "probe_unavailable"}
        current: CurrentVersion = {}
        try:
            binding = await asyncio.to_thread(
                self._bindings.get_active_by_bot_and_owner,
                bot_id,
                owner_id,
            )
            if binding is None:
                detection = {"status": "read_failed", "reason": "device_not_bound"}
            else:
                selected = await self._select_instance(binding, device_uuid)
                if selected:
                    context["instance_id"] = selected
                ctx = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._resolver.resolve_for_binding_invoke,
                        binding.id,
                        owner_id,
                        bot_id=bot_id,
                        device_uuid=selected
                        if binding.device_provider in {"baas", "teclaw"}
                        else None,
                    ),
                    timeout=10.0,
                )
                detection, current = await self._probe(ctx.conn_info)
        except EngineVersionInstanceSelectionError:
            raise
        except TimeoutError:
            detection = {"status": "read_failed", "reason": "probe_timeout"}
        except Exception:
            # External errors can contain credentials/addresses. Publish controlled reasons.
            detection = {"status": "read_failed", "reason": "probe_unavailable"}
        comparison = compare(detection, current, target)
        result: EngineVersionResult = {
            "detection": detection,
            "release_target": target,
            "comparison": comparison,
            "restart_hint": "recommended"
            if comparison in {"older", "legacy"}
            else ("none" if comparison in {"same", "newer"} else "unknown"),
            "context": context,
        }
        if current:
            result["current"] = current
        return result

    async def _select_instance(self, binding, requested: str | None) -> str:
        if binding.device_provider not in {"arca", "baas", "teclaw", "local"}:
            raise ValueError("unsupported deployment")
        if binding.device_provider in {"arca", "local"}:
            if requested is not None and requested != binding.device_id:
                raise EngineVersionInstanceSelectionError("instance_not_found")
            return binding.device_id
        # Freeze an instance before probing. Never let /http-info silently pick
        # the first device of a multi-instance binding or fall back after a miss.
        devices = await asyncio.wait_for(
            asyncio.to_thread(
                self._list_devices,
                binding.device_id,
            ),
            timeout=10.0,
        )
        ids = {
            device["device_uuid"]
            for device in devices
            if isinstance(device, dict)
            and isinstance(device.get("device_uuid"), str)
            and device["device_uuid"]
        }
        if requested is not None:
            if requested not in ids:
                raise EngineVersionInstanceSelectionError("instance_not_found")
            return requested
        if len(ids) > 1:
            raise EngineVersionInstanceSelectionError("instance_selection_required")
        if not ids:
            raise ValueError("no device")
        return ids.pop()
