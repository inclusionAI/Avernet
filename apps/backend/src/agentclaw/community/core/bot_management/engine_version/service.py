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
from agentclaw.community.core.engine_runtime.gate import resolve_operator_level
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import resolve_operable_permission_level
from agentclaw.community.core.bot_inventory.protocols import ServiceLifecyclePort
from agentclaw.community.core.bot_inventory.types import BotAction, DisplayState
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
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
        lifecycle: ServiceLifecyclePort,
    ) -> None:
        self._bots = bots
        self._bindings = bindings
        self._resolver = resolver
        self._probe = probe
        self._collaborators = collaborators
        self._list_devices = list_devices
        self._config = config
        self._lifecycle = lifecycle

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
        # Visibility is broader than operation permission: inventory Space members
        # and explicit collaboration-directory readers may receive an advisory
        # refusal, but strangers must still get the same masked 404 as absence.
        await asyncio.to_thread(self._require_visible, bot, caller_id, owner_id)
        context: VersionContext = {
            "bot_id": bot_id,
            "stage": stage,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        target = release_target(self._config.target_release)

        def restricted(reason: str) -> EngineVersionResult:
            return {
                "detection": {"status": "not_applicable", "reason": reason},
                "release_target": {"status": "unavailable", "reason": reason},
                "comparison": "unknown",
                "restart_hint": "restricted",
                "restart_reason": reason,
                "context": context,
            }

        level = await asyncio.to_thread(
            resolve_operator_level, self._collaborators,
            bot=bot, caller_id=caller_id, owner_id=owner_id,
        )
        if level < PermissionLevel.MEMBER:
            return restricted("restart_not_permitted")
        if stage != "draft":
            raise EngineVersionStageUnsupportedError("stage_not_supported")
        if bot.get("bot_type") not in {"personal", "service"}:
            return restricted("bot_type_not_supported")
        if bot.get("active_engine") not in {"openclaw", "hermes"}:
            return {
                "detection": {"status": "not_applicable", "reason": "engine_not_supported"},
                "release_target": target,
                "comparison": "unknown",
                "restart_hint": "none",
                "context": context,
            }
        if bot.get("status") not in {"ACTIVE", "FAILED"}:
            return restricted("bot_not_ready")
        if bot.get("bot_type") == "service":
            cards = await asyncio.to_thread(self._lifecycle.cards_for_bots, bots=[bot])
            matches = cards.get(bot_id, ())
            # This endpoint addresses the draft runtime. Historical or online
            # publication cards do not revoke an independently restartable draft.
            if not any(
                card.display_state == DisplayState.SERVICE_DRAFT
                and BotAction.RESTART in card.actions for card in matches
            ):
                return restricted("service_not_draft")
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

    def _require_visible(self, bot: dict[str, Any], caller_id: str, owner_id: str) -> None:
        if caller_id == owner_id:
            return
        try:
            effective = resolve_operable_permission_level(
                self._collaborators, bot=bot, user_id=caller_id, owner_id=owner_id,
            )
            # The collaboration directory returns explicit records even after
            # Space operation revocation. This proves visibility, never restart.
            listed = self._collaborators.get_permission_level(
                int(bot["id"]), caller_id, owner_id,
            )
            if max(effective, PermissionLevel(listed)) >= PermissionLevel.MEMBER:
                return
        except Exception:
            pass  # No visibility proof on unavailable permission storage.
        raise BotNotFoundError("Bot not found")

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
