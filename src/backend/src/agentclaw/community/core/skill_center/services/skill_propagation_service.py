"""SkillPropagationService — 发布态变更的运行时传播服务。

发布侧（七桃）完成状态机变更后调用此处即可，内部负责：
  1. 计算影响 Bot 范围（Phase 2：依赖 skill_uuid schema 落地后补全）
  2. 触发 SkillCenterSyncService 拉取/清理 NAS 产物（upgrade 时调用 _sync_nas_artifact，异常仅记录不阻塞）
  3. 对每个受影响 Bot 刷软链（已实装）
  4. 幂等 + 写 propagation_log + 失败不阻塞发布事务

NAS 拉取与软链下发已实装。Phase 2 补全影响 Bot 计算，依赖七桃的 skill_uuid / status schema 改动。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.skill_center.services.repositories import SkillSetRepository
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher

logger = get_logger()

_IDEMPOTENT_WINDOW_SECONDS = 30


@dataclass
class PropagationResult:
    """传播结果（仅用于日志/观测，不影响发布事务）。"""
    affected_bot_count: int
    success_bot_count: int
    failed_bot_ids: list[str] = field(default_factory=list)
    propagation_log_id: str | None = None


@runtime_checkable
class SkillPropagationLogRepository(Protocol):
    """ac_skill_propagation_log 存储接口。"""

    def create(self, data: dict) -> dict:
        """写入一条新 log 记录，返回含 propagation_id 的 dict。"""
        ...

    def update(self, propagation_id: str, data: dict) -> None:
        """按 propagation_id 更新 log 记录。"""
        ...

    def find_recent(self, skill_uuid: str, env: str, within_seconds: int) -> dict | None:
        """查找 within_seconds 内同 (skill_uuid, env) 的最新 done/pending 记录。"""
        ...


class SkillPropagationService:
    """发布态变更的运行时传播服务 —— 发布侧统一对外接口。

    七桃侧完成状态机变更后调用此处即可，不需要关心：
      - skills-center/ 下载和清理
      - sync_symlinks 如何下发
      - OpenClaw 热重载协议
      - 影响 Bot 列表的计算
    """

    def __init__(
        self,
        log_repo: SkillPropagationLogRepository,
        skill_set_repo: "SkillSetRepository",
        resolver: "DeviceContextResolver",
        device_sync_dispatcher: "DeviceSyncDispatcher",
        skill_set_service_factory: "SkillSetServiceFactory",
        sync_service=None,
    ):
        self._log_repo = log_repo
        self._sync_service = sync_service
        self._skill_set_repo = skill_set_repo
        self._resolver = resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._skill_set_service_factory = skill_set_service_factory

    # ── 公开接口 ────────────────────────────────────────────────────────

    def propagate_on_upgrade(
        self,
        skill_uuid: str,
        env: str,
        new_version: str,
    ) -> PropagationResult:
        """升级发布成功后调用。

        触发时机：七桃侧 pending → published 状态流转 且 是升级发布（非首次）。
        首次发布（first-time publish）无存量引用，不需要调用本接口。
        """
        return self._propagate(
            action="upgrade",
            skill_uuid=skill_uuid,
            env=env,
            extra={"new_version": new_version},
        )

    def propagate_on_removal(
        self,
        skill_uuid: str,
        env: str,
    ) -> PropagationResult:
        """Skill 删除/下架后调用。

        触发时机：七桃侧 Skill 物理删除，或 SC 端标记下架。
        """
        return self._propagate(
            action="removal",
            skill_uuid=skill_uuid,
            env=env,
            extra={},
        )

    # ── 内部实现 ────────────────────────────────────────────────────────

    def _propagate(self, *, action: str, skill_uuid: str, env: str, extra: dict) -> PropagationResult:
        """核心传播逻辑。全程 try/except 兜底，不抛异常。"""
        propagation_id = str(uuid.uuid4())
        try:
            # 1. 幂等检查：30s 内同 (skill_uuid, env) 已有 pending/done 记录则复用
            recent = self._log_repo.find_recent(skill_uuid, env, _IDEMPOTENT_WINDOW_SECONDS)
            if recent and recent.get("status") in ("pending", "done"):
                logger.info(
                    "[SkillPropagation] idempotent hit: skill_uuid=%s env=%s "
                    "existing_propagation_id=%s status=%s",
                    skill_uuid, env, recent.get("propagation_id"), recent.get("status"),
                )
                return PropagationResult(
                    affected_bot_count=recent.get("affected_bot_count", 0),
                    success_bot_count=recent.get("success_bot_count", 0),
                    failed_bot_ids=json.loads(recent.get("failed_bot_ids") or "[]"),
                    propagation_log_id=recent.get("propagation_id"),
                )

            # 2. 写 log（pending）
            self._log_repo.create({
                "propagation_id": propagation_id,
                "skill_uuid": skill_uuid,
                "env": env,
                "action": action,
                "status": "pending",
                "affected_bot_count": 0,
                "success_bot_count": 0,
                "failed_bot_ids": "[]",
                "extra": json.dumps(extra),
            })
            logger.info(
                "[SkillPropagation] started: propagation_id=%s action=%s "
                "skill_uuid=%s env=%s extra=%s",
                propagation_id, action, skill_uuid, env, extra,
            )

            # 3. 计算影响 Bot 范围
            # Phase 1 骨架：返回空列表。
            # Phase 2 将补全：依赖七桃的 skill_uuid schema 落地（合约 §6.1 / §6.2）。
            affected_bots: list[dict] = self._find_affected_bots(skill_uuid, env)

            # 3.5 触发 NAS 同步（仅 upgrade）
            if action == "upgrade":
                self._sync_nas_artifact(skill_uuid, env)

            # 4. 逐 Bot 刷软链（Phase 1 跳过，affected_bots 为空）
            failed_bot_ids: list[str] = []
            success_count = 0
            for bot in affected_bots:
                ok = self._refresh_bot(
                    bot["bot_id"],
                    bot["owner_id"],
                    skill_uuid,
                    bot.get("engine_type", "openclaw"),
                    env,
                )
                if ok:
                    success_count += 1
                    self._notify_hot_reload(bot["bot_id"], skill_uuid)
                else:
                    failed_bot_ids.append(bot["bot_id"])

            # 5. 更新 log（done）
            self._log_repo.update(propagation_id, {
                "status": "done",
                "affected_bot_count": len(affected_bots),
                "success_bot_count": success_count,
                "failed_bot_ids": json.dumps(failed_bot_ids),
            })
            logger.info(
                "[SkillPropagation] done: propagation_id=%s affected=%d "
                "success=%d failed=%s",
                propagation_id, len(affected_bots), success_count, failed_bot_ids,
            )
            return PropagationResult(
                affected_bot_count=len(affected_bots),
                success_bot_count=success_count,
                failed_bot_ids=failed_bot_ids,
                propagation_log_id=propagation_id,
            )

        except Exception as exc:
            logger.exception(
                "[SkillPropagation] unexpected error: propagation_id=%s "
                "skill_uuid=%s env=%s exc=%s",
                propagation_id, skill_uuid, env, exc,
            )
            try:
                self._log_repo.update(propagation_id, {
                    "status": "failed",
                    "error_msg": str(exc)[:500],
                })
            except Exception:
                pass
            return PropagationResult(
                affected_bot_count=0,
                success_bot_count=0,
                failed_bot_ids=[],
                propagation_log_id=propagation_id,
            )

    def _find_affected_bots(self, skill_uuid: str, env: str) -> list[dict]:
        """返回所有受 skill_uuid 影响的 bot。

        语义：仅普通 active SkillSet（is_default=0、is_active=1、bolt_id 非空）所属、
        且未删除的 bot。default SkillSet 不主动传播，依赖会话启动兜底。
        engine_type 取 ac_bots.active_engine（source of truth）。
        """
        try:
            bots = self._skill_set_repo.find_affected_bots_by_skill_uuid(skill_uuid, env=env)
            result = [
                {
                    "bot_id": b["bot_id"],
                    "owner_id": str(b["owner_id"]) if b.get("owner_id") else "",
                    "engine_type": b.get("active_engine") or "openclaw",
                }
                for b in bots
            ]
            logger.info(
                "[SkillPropagation] _find_affected_bots: skill_uuid=%s bots=%d",
                skill_uuid, len(result),
            )
            return result
        except Exception as exc:
            logger.warning(
                "[SkillPropagation] _find_affected_bots failed (returning empty): %s", exc,
            )
            return []

    def _refresh_bot(self, bot_id: str, owner_id: str, skill_uuid: str, engine_type: str = "openclaw", env: str | None = None) -> bool:
        """对单个 Bot 刷新软链。失败返回 False，不抛异常。

        Reachable in prod: ``_find_affected_bots`` is fully implemented
        (queries ``find_affected_bots_by_skill_uuid``), so this runs
        whenever a propagated skill has active bots. Builds the
        per-bot ``SkillSetService`` via the DI factory —
        ``SkillSetService.__init__`` requires injected repos/plugins the
        bare constructor cannot supply.
        """
        try:
            svc = self._skill_set_service_factory.create(
                user_id=owner_id,
                entity_id=owner_id,
                bot_id=bot_id,
                engine_type=engine_type,
            )
            mappings = svc.get_symlink_mappings(user_id=owner_id, bolt_id=bot_id)
            ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
            device_sync = self._device_sync_dispatcher.dispatch(ctx)
            result = device_sync.sync_symlinks([m.to_dict() for m in mappings])
            ok = bool(result.get("success"))
            logger.info(
                "[SkillPropagation] _refresh_bot: bot_id=%s skill_uuid=%s ok=%s result=%s",
                bot_id, skill_uuid, ok, result,
            )
            return ok
        except Exception as exc:
            logger.exception(
                "[SkillPropagation] _refresh_bot failed: bot_id=%s skill_uuid=%s exc=%s",
                bot_id, skill_uuid, exc,
            )
            return False

    def _sync_nas_artifact(self, skill_uuid: str, env: str) -> None:
        """触发 NAS 产物同步（仅 upgrade 时调用）。失败仅记录，不阻塞软链刷新。"""
        if self._sync_service is None:
            logger.info(
                "[SkillPropagation] sync_service unavailable, skip NAS sync: "
                "skill_uuid=%s env=%s", skill_uuid, env,
            )
            return
        try:
            self._sync_service.force_sync(skill_uuid=skill_uuid, env=env)
            logger.info(
                "[SkillPropagation] NAS sync ok: skill_uuid=%s env=%s",
                skill_uuid, env,
            )
        except Exception as exc:
            logger.exception(
                "[SkillPropagation] NAS sync failed (non-blocking): "
                "skill_uuid=%s env=%s exc=%s",
                skill_uuid, env, exc,
            )

    def _notify_hot_reload(self, bot_id: str, skill_uuid: str) -> None:
        """通知 OpenClaw 热重载。

        TBD-14：协议未定，MVP 仅打日志占位，依赖下次 Bot 启动时
        `SkillSetService._sync_symlinks_to_device_if_needed` 主动推送。
        """
        logger.info(
            "[SkillPropagation][hot-reload pending] bot_id=%s skill_uuid=%s "
            "(协议 TBD-14；当前依赖下次 skill 状态变更主动推送)",
            bot_id, skill_uuid,
        )
