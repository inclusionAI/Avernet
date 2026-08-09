"""Local device lifecycle helpers — startup/shutdown hooks for local (SQLite) mode.

这些函数在 app.py 的 startup/shutdown 事件中调用，负责清理 stale 的设备绑定
和重新分配孤立的 Bot。放在 plugins/local/ 是因为它们直接操作 SQLite，
属于 local 模式的基础设施层，不应放在 core/ 层。
"""
import threading

from agentclaw.community.core.devices.models import DeviceBindingStatus as DBS
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding

logger = get_logger()

_LOCAL_DEVICE_PROVIDER = "local"


def release_all_stale_bindings(database: DatabasePlugin) -> None:
    """Release all local device bindings and reset associated bots.

    Local processes die when the backend exits, leaving stale DB records.
    Called from shutdown hook and the startup hook (crash recovery).
    """
    try:
        with database.orm_session() as db:
            stale_bindings = db.query(EntityDeviceBinding).filter(
                EntityDeviceBinding.device_provider == _LOCAL_DEVICE_PROVIDER,
                EntityDeviceBinding.status.in_([
                    DBS.ACTIVE.value,
                    DBS.PENDING.value,
                ]),
            ).all()

            if not stale_bindings:
                logger.info(
                    "[release_all_stale_bindings] No stale local bindings found"
                )
                return

            binding_ids = [b.id for b in stale_bindings]
            logger.info(
                f"[release_all_stale_bindings] Found {len(stale_bindings)} "
                f"stale local device binding(s): {binding_ids}"
            )

            for binding in stale_bindings:
                binding.status = DBS.RELEASED.value

            bots = db.query(BotModel).filter(
                BotModel.binding_id.in_(binding_ids),
                BotModel.is_delete == 0,
            ).all()

            for bot in bots:
                logger.info(
                    f"[release_all_stale_bindings] Resetting bot {bot.bot_id}: "
                    f"status={bot.status} -> PENDING, clearing binding"
                )
                bot.status = "PENDING"
                bot.binding_id = None
                bot.device_id = None

            db.commit()
            logger.info(
                f"[release_all_stale_bindings] Released {len(stale_bindings)} "
                f"binding(s), reset {len(bots)} bot(s)"
            )
    except Exception as e:
        logger.error(f"[release_all_stale_bindings] Failed: {e}")


def reallocate_orphaned_bots(
    database: DatabasePlugin,
    bot_service,
) -> None:
    """Find PENDING bots with no device binding and trigger allocation.

    Called from the startup hook AFTER release_all_stale_bindings(). The
    caller resolves ``BotService`` from the app injector and passes it in.
    """
    try:
        with database.session() as db:
            orphaned_bots = db.query(BotModel).filter(
                BotModel.status == "PENDING",
                BotModel.binding_id.is_(None),
                BotModel.is_delete == 0,
            ).all()

            if not orphaned_bots:
                logger.info(
                    "[reallocate_orphaned_bots] No orphaned PENDING bots found"
                )
                return

            # 桌面 bot 的设备分配走 BaaS 流程（device_id = BOT-xxx），
            # 不应走 DeviceService.apply_device()（会生成 staff_xxx 格式 device_id）。
            # 过滤掉桌面 bot，避免 device_id 被覆盖
            bot_snapshots = [
                {
                    "bot_id": bot.bot_id,
                    "owner_id": bot.owner_id,
                    "entity_id": bot.entity_id,
                    "entity_type": bot.entity_type,
                    "bot_name": bot.bot_name,
                    "active_engine": bot.active_engine,
                }
                for bot in orphaned_bots
                if bot.bot_type != "desktop"
            ]

            skipped = len(orphaned_bots) - len(bot_snapshots)
            if skipped:
                logger.info(
                    f"[reallocate_orphaned_bots] Skipped {skipped} desktop "
                    "bot(s) (desktop bots use BaaS device allocation, not "
                    "DeviceService)"
                )

            logger.info(
                f"[reallocate_orphaned_bots] Found {len(bot_snapshots)} orphaned "
                f"PENDING bot(s), triggering device allocation"
            )

        # BotService opens repository Sessions while allocating. The read
        # context above must be closed first so local StaticPool never nests
        # those Sessions on its one shared connection.
        _reallocate_bots(bot_snapshots, bot_service)

    except Exception as e:
        logger.error(f"[reallocate_orphaned_bots] Failed: {e}")


def _reallocate_bots(bot_snapshots: list[dict], bot_service) -> None:
    """Trigger device allocation for a list of bot snapshots."""
    default_engine = "openclaw"

    for snapshot in bot_snapshots:
        def reallocate_one(s=snapshot):
            try:
                logger.info(
                    f"[_reallocate_bots] Re-allocating device for bot {s['bot_id']}, "
                    f"owner={s['owner_id']}, entity={s['entity_id']}"
                )
                bot_service._allocate_device_async(
                    bot_id=s["bot_id"],
                    user_id=s["owner_id"],
                    nick_name=s["owner_id"],
                    entity_id=s["entity_id"],
                    entity_type=s["entity_type"],
                    engine_types=["openclaw"],
                    bot_name=s["bot_name"],
                    active_engine=s["active_engine"] or default_engine,
                    owner_id=s["owner_id"],
                )
                logger.info(f"[_reallocate_bots] Device allocation started for bot {s['bot_id']}")
            except Exception as e:
                logger.error(f"[_reallocate_bots] Failed to reallocate bot {s['bot_id']}: {e}")

        try:
            thread = threading.Thread(target=reallocate_one, daemon=True)
            thread.start()
        except Exception as e:
            logger.error(f"[_reallocate_bots] Failed to start thread for bot {snapshot['bot_id']}: {e}")
