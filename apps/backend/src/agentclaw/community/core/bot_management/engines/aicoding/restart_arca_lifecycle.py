"""Coding-owned Arca replacement orchestration over existing stop/start primitives.

Only admitted, bound coding restarts enter here. Unbound recovery and other
engines retain the legacy lifecycle. Allocation still owns lock release after
handoff; this strategy must not unlock a successfully spawned allocation.
"""
from __future__ import annotations

from agentclaw.community.utils.env_utils import get_current_env

from .restart_arca import RestartBackupTargetChanged


class AicodingArcaRestart:
    def __init__(self, service):
        self.service = service

    def execute(self, bot, execution):
        from ..registry import resolve_restart_strategy

        service, state = self.service, execution.state
        if (
            not bot.get("entity_id")
            or bot.get("bot_type") == "desktop"
            or bot.get("status") not in {"ACTIVE", "PENDING", "FAILED"}
            or (bot.get("device_binding") or {}).get("device_provider") != "arca"
        ):
            raise RestartBackupTargetChanged("Arca restart lifecycle changed")
        ctx, strategy = resolve_restart_strategy(bot)
        execution.check_target(ctx, bot["binding_id"])
        target_provider = service._resolve_restart_target_provider(
            bot_id=state.bot_id, user_id=state.owner_id, bot=bot,
            source_provider="arca",
        )
        # Wait outside the short-lived restart lock. The returned verifier
        # rechecks the old binding/sandbox and fences mutation under the lock.
        verify = strategy.prepare_restart(
            ctx, binding_id=bot["binding_id"],
            device_service_provider=service._device_service_provider,
            arca_restart_transport_provider=service._arca_restart_transport_provider,
            bot_repository=service._repository,
        )
        env, entity_id = get_current_env(), bot["entity_id"]
        lock = service._try_acquire_restart_lock(
            env, entity_id, state.bot_id, state.owner_id,
        )
        if lock is None:
            return state.read()  # No mutation fence: the worker can retry.
        handed_off = False
        try:
            verify()
            if bot.get("bot_type") == "service":
                bot = service._mark_service_bot_default_image(bot)
            release_ok = service.stop_bot(
                bot_id=state.bot_id, user_id=state.owner_id,
                nick_name=execution.payload.get("nick_name"),
                release_reason=f"Bot {state.bot_id} restarted",
            )
            result = service.start_bot(
                bot_id=state.bot_id, user_id=state.owner_id,
                nick_name=execution.payload.get("nick_name"),
                device_provider=target_provider,
                restart_lock_key=(env, entity_id, state.bot_id, lock.lock_token),
                bot_ext_override=(
                    bot.get("ext")
                    if bot.get("bot_type") == "service"
                    and (bot.get("ext") or {}).get("sbot_use_default_image") is True
                    else None
                ),
                # Admission already applied engine-owned restart configuration.
                extra_configs=None,
            )
            handed_off = True
            if not release_ok:
                result["restart_warning"] = "旧设备释放可能失败，如后续出现设备限制问题，请联系管理员"
            return result
        finally:
            if not handed_off:
                service._restart_lock_repo.release(
                    env, entity_id, state.bot_id, lock.lock_token,
                )
