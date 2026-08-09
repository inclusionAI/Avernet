"""OssToNas Switch Service — OSS → NAS 切换业务编排服务.

封装单个/批量 bot 从 OSS 切换到 NAS 的完整多步骤业务流程。

The module-level functions below are the historical entry points. The
:class:`OssToNasSwitchService` class at the bottom is the
Service-API-shaped wrapper exposed via DI; it holds the cross-cutting
deps as instance attrs so HTTP adapters can call
``service.switch_one(...)`` without re-threading every kwarg.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.repository.protocols.devices import OssToNasRecordRepository
    from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
    from agentclaw.community.di import config as _cfg

logger = get_logger()


def _resolve_device_provider_before_stop(
    bot_info: dict[str, Any],
    *,
    device_binding_repo: DeviceBindingRepository | None,
    staff_no: str,
    bot_id: str,
) -> str | None:
    """读取当前 binding provider，供 stop 后重新 start 时透传。"""
    binding_id = bot_info.get("binding_id")
    if not binding_id:
        return None

    if device_binding_repo is None:
        raise RuntimeError(
            f"Bot {bot_id}/{staff_no} has binding_id={binding_id}, "
            "but device binding repo is not available"
        )

    binding = device_binding_repo.get_by_id(int(binding_id))
    if not binding:
        raise RuntimeError(
            f"Bot {bot_id}/{staff_no} binding {binding_id} not found"
        )

    if isinstance(binding, dict):
        provider = binding.get("device_provider")
    else:
        provider = getattr(binding, "device_provider", None)
    if not provider:
        raise RuntimeError(
            f"Bot {bot_id}/{staff_no} binding {binding_id} missing device_provider"
        )
    return str(provider)


async def do_switch_one(
    staff_no: str,
    bot_id: str,
    env: str | None = None,
    *,
    bot_repo: BotRepository,
    bot_service: BotService,
    oss_to_nas_config: _cfg.OssToNasConfig,
    record_repo: OssToNasRecordRepository,
    device_binding_repo: DeviceBindingRepository | None = None,
) -> dict[str, Any]:
    """执行单个 bot 的 OSS → NAS 切换流程。"""
    if env is None:
        from agentclaw.community.utils.env_utils import get_current_env
        env = get_current_env()

    try:
        record_repo.update_status(staff_no, bot_id, "switching", env=env)
        logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Status → switching")

        try:
            bot_info = bot_repo.get_by_id_and_owner(bot_id, staff_no)
            if not bot_info:
                raise RuntimeError(f"Bot not found: {bot_id}/{staff_no}")

            entity_type = bot_info.get("entity_type", "staff")
            entity_id = bot_info.get("entity_id", staff_no)
            bot_type = bot_info.get("active_engine", "openclaw")
            current_device_provider = _resolve_device_provider_before_stop(
                bot_info,
                device_binding_repo=device_binding_repo,
                staff_no=staff_no,
                bot_id=bot_id,
            )

            bot_service.stop_bot(bot_id=bot_id, user_id=staff_no)
            logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Bot stopped")
        except Exception as e:
            logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Failed to stop bot: {e}")
            raise

        try:
            from agentclaw.community.core.devices.services.oss_to_nas_migration_service import OssToNasMigrationService

            migration_service = OssToNasMigrationService(
                oss_to_nas_config=oss_to_nas_config,
            )
            success = await asyncio.to_thread(
                migration_service.migrate, env, entity_type, entity_id, bot_type, bot_id
            )
            if not success:
                raise RuntimeError(
                    f"数据迁移失败: env={env}, entity={entity_type}/{entity_id}, "
                    f"bot_type={bot_type}, bot_id={bot_id}"
                )
            logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Data migration complete")
        except Exception as e:
            logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Data migration failed: {e}")
            raise

        try:
            start_kwargs = {
                "bot_id": bot_id,
                "user_id": staff_no,
                "force_nas": True,
            }
            if current_device_provider is not None:
                start_kwargs["device_provider"] = current_device_provider
            bot_service.start_bot(**start_kwargs)
            logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Bot started")
        except Exception as e:
            logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Failed to start bot: {e}")
            raise

        record_repo.update_status(staff_no, bot_id, "nas", env=env)
        logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Status → nas (migration complete)")

        return {"staff_no": staff_no, "bot_id": bot_id, "status": "success"}

    except Exception as e:
        logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Migration failed: {e}")
        try:
            record_repo.update_status(staff_no, bot_id, "failed", env=env)
        except Exception as update_err:
            logger.error(
                f"[oss_to_nas] [{staff_no}/{bot_id}] Failed to update status to failed: {update_err}"
            )
        return {"staff_no": staff_no, "bot_id": bot_id, "status": "failed", "error": str(e)}


async def do_rollback_one(
    staff_no: str,
    bot_id: str,
    env: str | None = None,
    *,
    bot_repo: BotRepository,
    bot_service: BotService,
    oss_to_nas_config: _cfg.OssToNasConfig,
    record_repo: OssToNasRecordRepository,
    device_binding_repo: DeviceBindingRepository | None = None,
) -> dict[str, Any]:
    """执行单个 bot 的 NAS → OSS 回滚流程。"""
    if env is None:
        from agentclaw.community.utils.env_utils import get_current_env
        env = get_current_env()

    try:
        record_repo.update_status(staff_no, bot_id, "rolling_back", env=env)
        logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Status → rolling_back")

        try:
            bot_info = bot_repo.get_by_id_and_owner(bot_id, staff_no)
            if not bot_info:
                raise RuntimeError(f"Bot not found: {bot_id}/{staff_no}")

            entity_type = bot_info.get("entity_type", "staff")
            entity_id = bot_info.get("entity_id", staff_no)
            bot_type = bot_info.get("active_engine", "openclaw")
            current_device_provider = _resolve_device_provider_before_stop(
                bot_info,
                device_binding_repo=device_binding_repo,
                staff_no=staff_no,
                bot_id=bot_id,
            )

            bot_service.stop_bot(bot_id=bot_id, user_id=staff_no)
            logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Bot stopped")
        except Exception as e:
            logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Failed to stop bot: {e}")
            raise

        try:
            from agentclaw.community.core.devices.services.oss_to_nas_migration_service import OssToNasMigrationService

            migration_service = OssToNasMigrationService(
                oss_to_nas_config=oss_to_nas_config,
            )
            success = await asyncio.to_thread(
                migration_service.migrate, env, entity_type, entity_id, bot_type, bot_id,
                "nas_to_oss",
            )
            if not success:
                raise RuntimeError(
                    f"数据回滚同步失败: env={env}, entity={entity_type}/{entity_id}, "
                    f"bot_type={bot_type}, bot_id={bot_id}"
                )
            logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Data sync (NAS → OSS) complete")
        except Exception as e:
            logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Data sync (NAS → OSS) failed: {e}")
            raise

        try:
            start_kwargs = {
                "bot_id": bot_id,
                "user_id": staff_no,
            }
            if current_device_provider is not None:
                start_kwargs["device_provider"] = current_device_provider
            bot_service.start_bot(**start_kwargs)
            logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Bot started")
        except Exception as e:
            logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Failed to start bot: {e}")
            raise

        record_repo.update_status(staff_no, bot_id, "oss", env=env)
        logger.info(f"[oss_to_nas] [{staff_no}/{bot_id}] Status → oss (rollback complete)")

        return {"staff_no": staff_no, "bot_id": bot_id, "status": "success"}

    except Exception as e:
        logger.error(f"[oss_to_nas] [{staff_no}/{bot_id}] Rollback failed: {e}")
        try:
            record_repo.update_status(staff_no, bot_id, "rollback_failed", env=env)
        except Exception as update_err:
            logger.error(
                f"[oss_to_nas] [{staff_no}/{bot_id}] Failed to update status to rollback_failed: {update_err}"
            )
        return {"staff_no": staff_no, "bot_id": bot_id, "status": "failed", "error": str(e)}


async def batch_switch_with_concurrency(
    records: list[dict[str, Any]],
    concurrency: int,
    env: str | None = None,
    *,
    bot_repo: BotRepository,
    bot_service: BotService,
    oss_to_nas_config: _cfg.OssToNasConfig,
    record_repo: OssToNasRecordRepository,
    device_binding_repo: DeviceBindingRepository | None = None,
) -> dict[str, Any]:
    """按并发数批量执行 OSS → NAS 切换。"""
    semaphore = asyncio.Semaphore(concurrency)

    async def limited_switch(record: dict[str, Any]):
        async with semaphore:
            return await do_switch_one(
                record["staff_no"],
                record["bot_id"],
                env=env,
                bot_repo=bot_repo,
                bot_service=bot_service,
                oss_to_nas_config=oss_to_nas_config,
                record_repo=record_repo,
                device_binding_repo=device_binding_repo,
            )

    results = await asyncio.gather(
        *[limited_switch(r) for r in records],
        return_exceptions=True,
    )

    succeeded = []
    failed = []
    for r in results:
        if isinstance(r, Exception):
            failed.append({"error": str(r)})
        elif r.get("status") == "success":
            succeeded.append(r)
        else:
            failed.append(r)

    return {
        "total": len(records),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "failed_details": failed,
    }


class OssToNasSwitchService:
    """Stateful wrapper around the module-level switch functions.

    Holds the cross-cutting deps once at construction time so HTTP
    routers can call ``service.switch_one(...)`` without threading the
    repository / config kwargs through every endpoint.

    Constructed via the explicit ``oss_to_nas_switch_service`` provider in
    :class:`agentclaw.community.di.modules.devices_module.DevicesModule` — no
    ``@inject`` decorator needed, so the constructor's TYPE_CHECKING
    annotations don't have to resolve at runtime.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        bot_service: BotService,
        record_repo: OssToNasRecordRepository,
        device_binding_repo: DeviceBindingRepository,
        oss_to_nas_config: _cfg.OssToNasConfig,
    ) -> None:
        self._bot_repo = bot_repo
        self._bot_service = bot_service
        self._record_repo = record_repo
        self._device_binding_repo = device_binding_repo
        self._oss_to_nas_config = oss_to_nas_config

    async def switch_one(
        self, staff_no: str, bot_id: str, env: str | None = None
    ) -> dict[str, Any]:
        return await do_switch_one(
            staff_no,
            bot_id,
            env,
            bot_repo=self._bot_repo,
            bot_service=self._bot_service,
            oss_to_nas_config=self._oss_to_nas_config,
            record_repo=self._record_repo,
            device_binding_repo=self._device_binding_repo,
        )

    async def rollback_one(
        self, staff_no: str, bot_id: str, env: str | None = None
    ) -> dict[str, Any]:
        return await do_rollback_one(
            staff_no,
            bot_id,
            env,
            bot_repo=self._bot_repo,
            bot_service=self._bot_service,
            oss_to_nas_config=self._oss_to_nas_config,
            record_repo=self._record_repo,
            device_binding_repo=self._device_binding_repo,
        )

    async def batch_switch_with_concurrency(
        self,
        records: list[dict[str, Any]],
        concurrency: int,
        *,
        env: str,
    ) -> dict[str, Any]:
        return await batch_switch_with_concurrency(
            records,
            concurrency,
            env=env,
            bot_repo=self._bot_repo,
            bot_service=self._bot_service,
            oss_to_nas_config=self._oss_to_nas_config,
            record_repo=self._record_repo,
            device_binding_repo=self._device_binding_repo,
        )
