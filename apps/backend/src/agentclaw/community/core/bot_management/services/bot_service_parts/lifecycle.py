"""
``stop_bot`` / ``update_status`` / ``start_bot`` and the async-restart handoff
— moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotLifecycleMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from typing import Any, Dict, Optional, Tuple

from agentclaw.community.core.bot_management.bcn_identity import provider_address_kwargs
from agentclaw.community.core.bot_management.collaboration_repair.profile import (
    outbound_avatar_from_record,
)
from agentclaw.community.core.bot_management.engines.registry import resolve_restart_strategy
from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE, _get_engine_types

from .common import (
    BotNotFoundError,
    BotServiceError,
    _compose_operator_context,
    logger,
)


class BotLifecycleMixin:
    """``stop_bot`` / ``update_status`` / ``start_bot`` and the async-restart handoff
    (``restart_bot_async``)."""

    def stop_bot(
        self,
        bot_id: str,
        user_id: str,
        nick_name: Optional[str] = None,
        release_reason: Optional[str] = None,
    ) -> bool:
        """Stop a bot by releasing its current device and resetting status to PENDING.

        Args:
            bot_id: Bot ID
            user_id: User ID (must be the owner)
            nick_name: Nick name for operator context (optional, defaults to user_id)
            release_reason: Reason for releasing device (optional)

        Returns:
            True if stopped successfully (including if device was already released),
            False if device release failed but status was still reset.

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If stop fails critically
        """
        if not user_id:
            raise BotServiceError("User ID is required for stopping bot")

        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        # 桌面 bot 的设备管理走 BaaS 流程，不应通过 DeviceService 释放，
        # 也不应清空 binding_id/device_id（桌面 bot 的 delete/restart 由 DesktopBotService 处理）。
        if bot.get("bot_type") == "desktop":
            raise BotServiceError(
                f"Desktop bot {bot_id} cannot be stopped via BotService.stop_bot, "
                f"use DesktopBotService instead"
            )

        resolved_nick_name = nick_name or user_id
        reason = release_reason or f"Bot {bot_id} stopped"

        # Release current device if binding exists
        binding_id = bot.get("binding_id")
        release_failed = False
        if binding_id:
            try:
                service = self._device_service_provider()
                binding = service.get_device(binding_id=binding_id)
                if binding and binding.status in [
                    DeviceBindingStatus.ACTIVE.value,
                    DeviceBindingStatus.PENDING.value,
                    DeviceBindingStatus.FAILED.value,
                    DeviceBindingStatus.STOPPED.value,
                ]:
                    operator = _compose_operator_context(user_id, resolved_nick_name)
                    service.release_device(
                        binding_id=binding_id,
                        release_reason=reason,
                        operator=operator,
                    )
                    logger.info(f"[bot_service.stop_bot] Released device for binding {binding_id}")
            except BotNotFoundError:
                logger.info(f"[bot_service.stop_bot] Device binding {binding_id} not found, treating as already released")
            except InvalidDeviceStatusError as e:
                logger.info(f"[bot_service.stop_bot] Device binding {binding_id} cannot be released: {e}")
            except Exception as e:
                logger.error(f"[bot_service.stop_bot] Failed to release device for binding {binding_id}: {e}")
                release_failed = True

        # Reset bot status to PENDING and clear binding info
        self._repository.update_by_owner(bot_id, user_id, {
            "status": "PENDING",
            "binding_id": None,
            "device_id": None,
        })
        logger.info(f"[bot_service.stop_bot] Bot {bot_id} status reset to PENDING")

        return not release_failed

    def update_status(self, bot_id: str, user_id: str, status: str) -> None:
        """Update the status column for a bot owned by user_id.

        Used by the dormant-bot recycling flow to mark a bot as RECYCLED
        after stop_bot has released its device.

        Args:
            bot_id: The bot whose status to update.
            user_id: Owner of the bot (used for ownership guard).
            status: New status string, e.g. ``'RECYCLED'``.
        """
        self._repository.update_by_owner(bot_id, user_id, {"status": status})
        logger.info(
            "[bot_service.update_status] bot_id=%s user_id=%s status=%s",
            bot_id, user_id, status,
        )

    def _resolve_current_device_restart_context(
        self,
        *,
        bot_id: str,
        binding_id: int,
    ) -> tuple[str, str | None]:
        """Return binding state while preserving its original provider."""
        try:
            service = self._device_service_provider()
            binding = service.get_device(binding_id=binding_id)
        except Exception as e:
            raise BotServiceError(
                f"Bot {bot_id} binding {binding_id} cannot resolve restart context: {e}"
            ) from e

        if isinstance(binding, dict):
            provider = binding.get("device_provider")
            binding_status = binding.get("status")
        else:
            provider = getattr(binding, "device_provider", None)
            binding_status = getattr(binding, "status", None)

        if not provider:
            raise BotServiceError(
                f"Bot {bot_id} binding {binding_id} missing device_provider; "
                "restart aborted to avoid creation rollout migration"
            )

        return str(provider), str(binding_status) if binding_status else None

    def _resolve_historical_unbound_restart_provider(
        self,
        *,
        bot_id: str,
        entity_id: str,
        entity_type: str,
        env: str,
    ) -> str | None:
        """Return a safe provider for a failed bot whose current binding is lost."""
        page = 1
        page_size = 100
        while True:
            total, bindings = self._device_binding_repo.list_bindings(
                env=env,
                entity_id=entity_id,
                entity_type=entity_type,
                status=None,
                page=page,
                page_size=page_size,
            )
            for binding in bindings:
                if (binding.device_props or {}).get("bolt_id") != bot_id:
                    continue

                status = str(binding.status or "").upper()
                if status not in {
                    DeviceBindingStatus.RELEASED.value,
                    DeviceBindingStatus.FAILED.value,
                    DeviceBindingStatus.STOPPED.value,
                }:
                    logger.warning(
                        "[bot_service.restart_bot] reject unbound recovery with live historical binding: "
                        "bot_id=%s binding_id=%s binding_status=%s",
                        bot_id,
                        binding.id,
                        status or "UNKNOWN",
                    )
                    return None

                provider = str(binding.device_provider or "")
                if provider:
                    logger.info(
                        "[bot_service.restart_bot] recover failed unbound bot from historical provider: "
                        "bot_id=%s binding_id=%s provider=%s binding_status=%s",
                        bot_id,
                        binding.id,
                        provider,
                        status,
                    )
                    return provider
                return None

            if not bindings or page * page_size >= total:
                return None
            page += 1

    @staticmethod
    def _activation_in_progress_result(bot: Dict[str, Any]) -> Dict[str, Any]:
        """Return an idempotent response without mutating lifecycle state."""
        current = dict(bot)
        current["restart_in_progress"] = True
        current["message"] = "Bot activation is in progress"
        return current

    def is_teclaw_bot(self, active_engine: Optional[str]) -> bool:
        """Whether a bot with this engine runs in a teclaw container.

        Delegates to the canonical teclaw definition on
        :class:`TeclawProvisionService`, so callers don't scatter
        ``active_engine == "teclaw"`` string checks.
        """
        return self._teclaw_provision_provider().is_teclaw(active_engine)

    def start_bot(
        self,
        bot_id: str,
        user_id: str,
        nick_name: Optional[str] = None,
        force_nas: bool = False,
        device_provider: Optional[str] = None,
        restart_lock_key: Optional[Tuple[str, str, str, str]] = None,
        bot_ext_override: Optional[Dict[str, Any]] = None,
        extra_configs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Start a bot by triggering async device allocation.

        Args:
            bot_id: Bot ID
            user_id: User ID (must be the owner)
            nick_name: Nick name for device allocation (optional, defaults to user_id)
            force_nas: If True, force NAS storage path (used during OSS→NAS migration)
            device_provider: Explicit device_provider fact to preserve for an
                existing bot. Used by restart; normal create/start leaves it
                empty so creation rollout can decide.
            restart_lock_key: Restart idempotency lock identity to hand off to
                the async allocation thread (set only by the restart flow).

        Returns:
            Updated bot record with PENDING status (device allocation in progress)

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If start fails
        """
        if not user_id:
            raise BotServiceError("User ID is required for starting bot")

        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        engine_types = bot.get("engine_types", _get_engine_types())
        active_engine = bot.get("active_engine", DEFAULT_ENGINE_TYPE)
        bot_type = bot.get("bot_type") or "personal"
        template_type = bot.get("template_type")
        resolved_nick_name = nick_name or user_id
        bot_owner_id = bot.get("owner_id") or user_id

        # Every start attempts BCN registration before allocation; only mode varies.
        connection_mode = self._resolve_bcn_provider_connection_mode(
            active_engine=active_engine,
            bot_type=bot_type,
        )
        logger.info(
            f"[bot_service.start_bot] register bot to BCN as provider: "
            f"bot_id={bot_id} active_engine={active_engine} "
            f"bot_type={bot_type} template_type={template_type} "
            f"connection_mode={connection_mode}"
        )
        self._register_bot_to_bcn_as_provider(
            bot_id=bot_id,
            user_id=user_id,
            owner_workno=bot_owner_id,
            bot_name=bot.get("bot_name") or bot_id,
            bot_summary=bot.get("bot_desc") or "",
            connection_mode=connection_mode,
            avatar_url=outbound_avatar_from_record(
                bot, log_context="[bot_service.start_bot]"
            ),
            **provider_address_kwargs(bot),
            respect_drm=False,
        )
        logger.info(
            f"[bot_service.start_bot] start requested: bot_id={bot_id}, "
            f"user_id={user_id}, active_engine={active_engine}, bot_type={bot_type}, "
            f"template_type={template_type}, "
            f"explicit_device_provider={device_provider or '<empty>'}, force_nas={force_nas}"
        )

        # Trigger async device allocation
        self._allocate_device_async(
            bot_id=bot_id,
            user_id=user_id,
            nick_name=resolved_nick_name,
            entity_id=entity_id,
            entity_type=entity_type,
            engine_types=engine_types,
            bot_name=bot.get("bot_name"),
            active_engine=active_engine,
            owner_id=bot_owner_id,
            force_nas=force_nas,
            device_provider=device_provider,
            restart_lock_key=restart_lock_key,
            bot_ext_override=bot_ext_override,
            extra_configs=extra_configs,
        )

        # The allocation thread is now spawned and (for the restart flow) owns
        # releasing the lock. Do NOT raise past this point: a post-spawn failure
        # would let the caller's guard release the lock while the thread is still
        # allocating, opening a window for a concurrent restart. Any failure of
        # the re-read (empty result OR exception) falls back to the bot we
        # already loaded — it clearly exists (we read it above and just
        # triggered allocation).
        try:
            updated_bot = self._repository.get_by_id_and_owner(bot_id, user_id)
            if not updated_bot:
                raise BotNotFoundError(f"Bot not found on re-read: {bot_id}")
        except Exception as reread_err:
            logger.warning(
                f"[bot_service.start_bot] Bot {bot_id} re-read failed after allocation "
                f"was triggered ({reread_err}); returning the pre-allocation snapshot."
            )
            updated_bot = dict(bot)
            updated_bot["status"] = "PENDING"
        updated_bot["engine_types"] = engine_types
        logger.info(f"[bot_service.start_bot] Bot {bot_id} start initiated, device allocation in progress")
        return updated_bot

    async def restart_bot_async(self, **kwargs) -> Dict[str, Any]:
        """HTTP adapter entrypoint; retain the synchronous lifecycle and engine policy."""
        bot = self.get_bot(kwargs['bot_id'], kwargs['user_id'])
        ctx, strategy = resolve_restart_strategy(bot)
        from agentclaw.community.core.bot_management.engines.restart_contract import RestartServices

        return await strategy.execute_restart(
            ctx, self.restart_bot,
            services=RestartServices(
                repository=self._repository,
                task_queue=self._task_queue_service,
                get_bot=self.get_bot,
                template_service=self._template_service,
                lifecycle=self,
            ),
            **kwargs,
        )
