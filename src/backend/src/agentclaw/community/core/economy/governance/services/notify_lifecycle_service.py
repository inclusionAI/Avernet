"""[内核] NotifyLifecycleService — 通知发送状态机正常路径唯一驱动。

对齐工单机 ``GovernanceLifecycleService`` 的收口标准:通知发送状态机
(pending→sending→sent/failed)正常投递路径的状态推进经此服务,每次先 invoke
``GovernanceNotification`` 领域守卫方法(``mark_claimed``/``mark_sent``/
``mark_failed``,内部 ``transition_to`` 白名单校验,非法转移抛
``IllegalNotifyTransitionError``)再 save,复活既有死代码守卫。

依赖边界:
  - 上行(web):无(由编排服务 scan_service 调用)。
  - 下行(repo):经 ``domain/protocols.NotifyLogRepositoryProtocol`` 访问
    notify_log 仓储;不碰 ORM。
  - 横向(service):无。

设计要点:
  - ``claim`` 走 SQL CAS 原子领用(``claim_pending`` 原语保留为 driver 内部
    并发豁免)——领用这个并发敏感动作必须保 SQL CAS(否则两个 cron 并发会重复
    领用同一条通知重发);领用成功后再 ``mark_claimed`` 统一 guard 语义、返
    改后领域模型(driver 持 claim 后状态一致性)。
  - ``mark_sent`` / ``mark_failed`` 走领域往返:read 领域模型 → invoke 守卫
    方法 → ``save_notification`` 写回。
  - 批量/紧急路径(批量取消、紧急制动批量关、手动投递)**不**走本服务,直接
    走 repo SQL 原语(紧急而准确,SQL 原子 UPDATE + WHERE 守卫是最准确形态)。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from injector import inject

from agentclaw.community.core.economy.governance.domain.notification import (
    GovernanceNotification,
    IllegalNotifyTransitionError,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.repository.implementations.governance.notify_log import NotifyLogRepository

log = get_logger(__name__)


class NotifyLifecycleService:
    """通知发送状态机正常路径唯一驱动 —— 无状态、领域往返。

    经 DI 注入(``di/modules/economy_governance_module``);注入
    ``NotifyLogRepository``。单测可 Mock repo 脱 DB(领域 guard 不需真库)。
    """

    @inject
    def __init__(
        self,
        notify_repo: NotifyLogRepository,
    ) -> None:
        self._notify_repo = notify_repo

    # ── 正常投递路径(领域往返) ────────────────────────────────────────

    def claim(
        self,
        notification_id: str,
        *,
        now: datetime,
    ) -> GovernanceNotification | None:
        """领用 pending→sending(原子 CAS,并发安全)。

        内部走 repo ``claim_pending`` SQL CAS(``WHERE notify_status=PENDING``)
        保证并发领用原子性 —— 领用这个动作保 SQL CAS 作为 driver 内部豁免,
        避免并发两个 cron 重复领用同一条重发。领用成功后 re-read 领域模型
        (claim_pending 已把状态置 sending)返给调用方,供后续 send + mark_sent
        使用。

        Args:
            notification_id: 通知 ID。
            now: 当前时刻(写入 last_send_at)。

        Returns:
            改后的领域模型(sending 态);被并发抢/已非 pending → None。
        """
        claimed = self._notify_repo.claim_pending(notification_id, now)
        if not claimed:
            return None
        return self._notify_repo.get_by_notification_id(notification_id)

    def mark_sent(
        self,
        notification_id: str,
        *,
        external_message_id: str | None,
        sent_at: datetime,
    ) -> bool:
        """sending→sent(领域守卫 mark_sent)。

        read 领域模型 → ``notify.mark_sent()``(transition_to 白名单校验,
        非法则抛 IllegalNotifyTransitionError)→ ``save_notification`` 写回。
        找不到/guard 失败返 False(guard 失败记 log,不 raise —— 调用方
        scan_service 把失败当一次投递失败处理。

        Returns:
            True 找到并写回成功;False 找不到或状态非法。
        """
        notify = self._notify_repo.get_by_notification_id(notification_id)
        if notify is None:
            return False
        try:
            notify.mark_sent(external_message_id, sent_at)
        except IllegalNotifyTransitionError as exc:
            log.warning(
                "[NotifyLifecycle] mark_sent illegal transition for %s: %s",
                notification_id, exc,
            )
            return False
        return self._notify_repo.save_notification(notify)

    def mark_failed(
        self,
        notification_id: str,
        *,
        error: str,
        terminal: bool,
    ) -> bool:
        """sending→failed(终态)/ sending→pending(重试,领域守卫 mark_failed)。

        ``terminal=True`` → 终态 FAILED(达 ``_MAX_SEND_ATTEMPTS`` 封顶);
        ``terminal=False`` → 回退 PENDING 下次 cron 重试。均经领域守卫。
        找不到/guard 失败返 False。

        Returns:
            True 找到并写回成功;False 找不到或状态非法。
        """
        notify = self._notify_repo.get_by_notification_id(notification_id)
        if notify is None:
            return False
        try:
            notify.mark_failed(error, terminal=terminal)
        except IllegalNotifyTransitionError as exc:
            log.warning(
                "[NotifyLifecycle] mark_failed illegal transition for %s: %s",
                notification_id, exc,
            )
            return False
        return self._notify_repo.save_notification(notify)