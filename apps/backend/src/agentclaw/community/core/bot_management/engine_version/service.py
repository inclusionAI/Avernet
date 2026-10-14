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
    TemplateRepository,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)

from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
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
)
from .release import compare, detect, release_target
from .policy import has_version_override, restart_restriction


from .protocol import EngineVersionServiceProtocol


class EngineVersionService(EngineVersionServiceProtocol):
    def __init__(
        self,
        *,
        bots: BotRepository,
        templates: TemplateRepository,
        bindings: DeviceBindingRepository,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
        collaborators: CollaboratorServiceProtocol,
        list_devices: Callable[[str], list[dict[str, Any]]],
        config: EngineVersionConfig,
    ) -> None:
        self._bots = bots
        self._templates = templates
        self._bindings = bindings
        self._resolver = resolver
        self._transport = transport
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
                response = await self._transport.invoke(
                    ctx.conn_info,
                    "POST",
                    "/api/bash/exec",
                    body={
                        "cmd": "printenv AGENTCLAW_IMAGE_VERSION",
                        "cwd": "/home/admin/",
                        "timeout": 5,
                    },
                    timeout=10.0,
                )
                detection, current = detect(response)
        except EngineVersionInstanceSelectionError:
            raise
        except TimeoutError:
            detection = {"status": "read_failed", "reason": "probe_timeout"}
        except DeviceAdapterEndpointNotFoundError:
            detection = {
                "status": "read_failed",
                "reason": "probe_endpoint_unavailable",
            }
        except DeviceAdapterHTTPStatusError as exc:
            detection = {
                "status": "read_failed",
                "reason": (
                    "probe_endpoint_unavailable"
                    if exc.status_code == 501
                    else "probe_http_error"
                ),
            }
        except Exception:
            # External errors can contain credentials/addresses. Publish controlled reasons.
            detection = {"status": "read_failed", "reason": "probe_unavailable"}
        target = release_target(self._config.target_release)
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
        try:
            template_record = await asyncio.to_thread(
                self._templates.get_by_bot_id, bot_id
            )
            template = template_record.get("ext") or {} if template_record else {}
            if not isinstance(template, dict):
                raise ValueError("invalid template")
            if has_version_override(template):
                result["detection"] = {
                    "status": "read_failed",
                    "reason": "source_unverified",
                }
                result.pop("current", None)
                result["comparison"] = "unknown"
                result["restart_hint"] = "unknown"
            else:
                reason = restart_restriction(bot, template)
                if reason and comparison in {"older", "legacy"}:
                    result["restart_hint"] = "restricted"
                    result["restart_reason"] = reason
        except Exception:
            # Preserve completed version facts when image-policy reads fail.
            result["restart_hint"] = "unknown"
            result["restart_reason"] = "policy_unknown"
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
