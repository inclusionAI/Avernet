"""DiscoveryService — 任务主动发现编排核心。

编排完整流程：
1. ``TaskReader`` 读取已发现的待确认任务 (按 bot_id/owner_id/dt 过滤)
2. 为每个 bot 的所有任务通过 ``SessionInitiator`` 创建 engine session（获得 session_id）
   — 同时通过 WebSocket ``chat.send`` 注入发现提示消息
3. session 创建成功后通过 ``NotifySenderPlugin`` 投递通知（发现摘要 + session 链接）
4. 用户在前端确认后，由执行框架处理（不在本模块）

使用方式::

    service = DiscoveryService(
        reader=SqliteTaskReader("scripts/.dependencies/data/discovered_tasks.db"),
        session_initiator=CronRelaySessionInitiator(cron_relay),
        notify_sender=CommunityNotifySender(),
    )

    # discover — 为单个 bot 读取任务 + 创建 session + 注入消息 + 投递通知
    results = await service.discover(bot_id="bot-001", owner_id="u001", agent_id="bot-001")

    # discover_all_bots — 遍历所有 bot（由 scheduler 线程调用）
    results = await service.discover_all_bots()
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.repository.protocols.task import (
    TaskDiscoveryLockRepositoryProtocol,
)
from agentclaw.community.core.task.task_discovery.lock_models import (
    TaskDiscoveryLockRecord,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.protocols import (
    BotServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    SessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    TaskReader,
    SqliteTaskReader,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderEventType,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    # 仅作 __init__ 的类型注解；运行期按鸭子类型调用 create_work_order_event。
    from agentclaw.community.api.work_order_service import (
        WorkOrderServiceProtocol,
    )

logger = get_logger()

#: discover() 单次调用（session 创建 + WebSocket + 通知）的预估耗时上限。
#: per-bot 锁 TTL 应略大于该值，使崩机时 stale reaper 能在当前 cron 周期内恢复。
DISCOVERY_LOCK_TTL_SECONDS = 600


@dataclass
class DiscoveryResult:
    """单次发现流程的结果。"""

    task: DiscoveredTask
    session: Optional[DiscoverySession] = None
    notification_message: str = ""
    notification_sent: bool = False
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.session is not None and self.error is None


class DiscoveryService:
    """任务主动发现编排服务。

    将 TaskReader、SessionInitiator 和 NotifySenderPlugin 编排在一起，
    提供 "发现 → 创建 session+注入消息 → 通知" 流程。
    """

    def __init__(
        self,
        reader: TaskReader,
        session_initiator: SessionInitiator,
        notify_sender: NotifySenderPlugin,
        bot_service: BotServiceProtocol | None = None,
        discovery_lock_repo: TaskDiscoveryLockRepositoryProtocol | None = None,
        work_order_service: WorkOrderServiceProtocol | None = None,
    ):
        self._reader = reader
        self._session_initiator = session_initiator
        self._notify_sender = notify_sender
        self._bot_service = bot_service
        self._lock_repo = discovery_lock_repo
        #: 工单通知投递（直接领域调用 WorkOrderService）。注入时在发现流程里额外
        #: 把"待确认任务"写成一条 NOTICE 工单事件，落 ac_work_order_notification。
        self._work_order_service = work_order_service

        #: 最近的发现结果 (task_id → DiscoveryResult)，供外部查询
        self._discoveries: dict[str, DiscoveryResult] = {}

    def _try_acquire_lock(
        self, bot_id: str
    ) -> Optional[TaskDiscoveryLockRecord]:
        """尝试获取该 bot 当日的发现锁（与 _try_acquire_restart_lock 逻辑同构）。

        多机器 cron 同时 fire 时，所有机器都进入 ``discover_all_bots()``，
        在 per-bot 循环里竞争 ``acquire()``：
        恰好一台机器的 INSERT 成功 -> 调用 discover()
        其余机器拿到 None -> 跳过该 bot

        1. ``INSERT`` — 成功即持锁
        2. 冲突 -> 检查 stale -> stale 则 reap 并重新 INSERT
        3. 仍冲突 -> None（其他机器正在处理，或当日锁仍有效）

        Lock key: ``(env, bot_id, discovery_date)`` 每日唯一。
        Holder: ``HOSTNAME`` 环境变量或 ``socket.gethostname()``。
        TTL: ``DISCOVERY_LOCK_TTL_SECONDS`` (600s) — 崩机后 stale reaper 恢复。
        """
        env = get_current_env()
        today = datetime.now().strftime("%Y-%m-%d")
        holder = os.environ.get("HOSTNAME", socket.gethostname())

        rec = self._lock_repo.acquire(env, bot_id, today, holder)
        if rec is not None:
            return rec

        # acquire/check/acquire 不是事务性的，分层处理 stale 和已释放的锁。
        stale = self._lock_repo.get_if_stale(
            env, bot_id, today, DISCOVERY_LOCK_TTL_SECONDS
        )
        if stale is not None:
            logger.warning(
                "[task_discovery] Reaping stale discovery lock: env=%s, bot=%s, "
                "date=%s, created=%s",
                env, bot_id, today, stale.gmt_create,
            )
            # compare-and-delete：token 不匹配说明已被其他机器 reap+reacquire
            self._lock_repo.release(env, bot_id, today, stale.lock_token)

        return self._lock_repo.acquire(env, bot_id, today, holder)

    async def discover_all_bots(self) -> list[DiscoveryResult]:
        """遍历 db 中有待确认任务的 bot，为每个 bot 执行发现流程。

        由 scheduler 线程调用（通过 asyncio.run）。

        bot 列表 = ``discovered_tasks.db`` pending 任务提取的 bot ∩ ``list_bots()``
        返回的存活 bot —— db 里没数据不触发，bot 已删除也不瞎跑。

        TODO: 未来在两个集合交集的基础上，通过 dream mode 接口进一步缩小范围
        —— 只对开启了 dream mode 且任务发现 ready 的 bot 执行发现。
        """
        # 1) 从 db 读取所有 pending 任务，提取唯一 (bot_id, owner_id)
        pending = self._reader.read_pending_tasks()
        if not pending:
            logger.info("[task_discovery] no pending tasks in db, skipping discovery")
            return []

        db_bots: dict[str, tuple[str, str]] = {}  # bot_id → (bot_id, owner_id)
        for task in pending:
            if task.bot_id and task.owner_id and task.bot_id not in db_bots:
                db_bots[task.bot_id] = (task.bot_id, task.owner_id)

        # 2) 从 BotService 获取存活 bot 列表
        if self._bot_service is None:
            logger.warning("[task_discovery] no bot_service, cannot discover_all_bots")
            return []

        try:
            result = self._bot_service.list_bots(page=1, page_size=100)
        except Exception as exc:
            logger.error("[task_discovery] failed to list bots: %s", exc)
            return []

        live_bots = result.get("items", []) if isinstance(result, dict) else []
        live_bot_ids = {b.get("bot_id", "") for b in live_bots}

        # 3) 取交集：db 有 pending 任务 且 bot 存活
        # TODO: 交集基础上通过 dream mode 接口进一步过滤
        intersection = [
            db_bots[bid] for bid in db_bots if bid in live_bot_ids
        ]

        # 4) 按 owner_id 聚合 — 同一个 owner 只取第一个 bot 执行发现，
        #    避免同一用户多个 bot 重复发现
        seen_owners: set[str] = set()
        bots_to_discover: list[tuple[str, str]] = []
        for bot_id, owner_id in intersection:
            if owner_id not in seen_owners:
                seen_owners.add(owner_id)
                bots_to_discover.append((bot_id, owner_id))

        logger.info(
            "[task_discovery] scheduled discovery: db=%d bots, live=%d bots, "
            "intersection=%d, after owner aggregation=%d bot(s) (from %d pending tasks)...",
            len(db_bots), len(live_bots), len(intersection),
            len(bots_to_discover), len(pending),
        )

        all_results: list[DiscoveryResult] = []
        for bot_id, owner_id in bots_to_discover:
            # ---- Per-bot 分布式锁（多机器竞争占有）----
            # 多机器 cron 同时 fire，各自逐 bot 遍历。每个 bot 的 INSERT
            # 由 DB UNIQUE 约束原子仲裁：恰好一台机器成功 -> discover()
            # 其余 None -> 跳过该 bot。discover 完成后 finally 释放锁。
            lock: Optional[TaskDiscoveryLockRecord] = None
            if self._lock_repo is not None:
                lock = self._try_acquire_lock(bot_id)
                if lock is None:
                    logger.info(
                        "[task_discovery] bot=%s already being discovered by "
                        "another instance, skipping",
                        bot_id,
                    )
                    continue

            try:
                results = await self.discover(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    agent_id=bot_id,
                )
                all_results.extend(results)
            except Exception as exc:
                logger.error(
                    "[task_discovery] bot=%s failed: %s",
                    bot_id, exc, exc_info=True,
                )
            finally:
                if lock is not None:
                    self._lock_repo.release(
                        lock.env,
                        lock.bot_id,
                        lock.discovery_date,
                        lock.lock_token,
                    )

        logger.info(
            "[task_discovery] discovery complete: %d task(s) discovered across %d bot(s)",
            sum(1 for r in all_results if r.success),
            len(bots_to_discover),
        )
        return all_results

    async def discover(
        self,
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> list[DiscoveryResult]:
        """为单个 bot 执行发现流程（手动触发或遍历调用）。

        1. 读取该 bot 当天的待确认任务
        2. 为所有任务创建一个 engine session（extInfo 携带所有任务数据）
           — 同时通过 WebSocket 注入发现提示消息
        3. 发送通知（发现摘要 + session 链接）
        """
        dt = datetime.now().strftime("%Y-%m-%d")
        tasks = self._reader.read_pending_tasks_for_bot(bot_id, owner_id, dt)
        if not tasks:
            logger.info(
                "[task_discovery] no pending tasks for bot=%s owner=%s dt=%s",
                bot_id, owner_id, dt,
            )
            return []

        logger.info(
            "[task_discovery] discovered %d pending tasks for bot=%s",
            len(tasks), bot_id,
        )

        results: list[DiscoveryResult] = []
        for task in tasks:
            result = await self._discover_single(
                task,
                all_tasks=tasks,
                bot_id=bot_id,
                owner_id=owner_id,
                agent_id=agent_id,
                model=model,
            )
            results.append(result)
            self._discoveries[task.task_id] = result

        return results

    async def _discover_single(
        self,
        task: DiscoveredTask,
        *,
        all_tasks: list[DiscoveredTask],
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None,
    ) -> DiscoveryResult:
        """处理单个任务：创建 session+注入消息 → 发通知。"""
        try:
            session = await self._session_initiator.initiate_session(
                all_tasks,
                bot_id=bot_id,
                owner_id=owner_id,
                agent_id=agent_id,
                model=model,
            )

            # 通知走两个通道：外发卡片（NotifySender）+ 工单通知（WorkOrderService）。
            card_sent = self._send_notification(
                task, owner_id, session, len(all_tasks),
            )
            work_order_sent = self._send_work_order_event(task, owner_id, session)
            notification_sent = card_sent or work_order_sent

            logger.info(
                "[task_discovery] task %s → session %s "
                "(card_sent=%s, work_order_sent=%s, notified=%s)",
                task.task_id,
                session.session_id,
                card_sent,
                work_order_sent,
                notification_sent,
            )

            return DiscoveryResult(
                task=task,
                session=session,
                notification_sent=notification_sent,
            )
        except Exception as exc:
            logger.error(
                "[task_discovery] failed for task %s: %s",
                task.task_id, exc,
            )
            return DiscoveryResult(task=task, error=str(exc))

    def _send_notification(
        self,
        task: DiscoveredTask,
        user_id: str,
        session: DiscoverySession,
        task_count: int,
    ) -> bool:
        """通过 NotifySenderPlugin 投递通知，返回是否发送成功。

        NotifySenderPlugin Protocol 约定 send() 从不抛异常；
        返回 str 为消息 ID（成功），None 为失败。
        通知 body 是 bot 的「告知」：发现摘要 + 确认引导。
        deep_link 指向 session，用户点击后进入 session 确认。
        extra 携带通用交互卡片参数（不绑定具体服务商）。
        """
        session_url = session.session_url
        message = NotifyMessage(
            title="发现待确认任务",
            body=task.to_notification_body(task_count),
            recipient=user_id,
            deep_link=session_url,
            extra={
                "channel": "tc_card",
                "card_template_id": os.environ.get(
                    "TASK_DISCOVERY_CARD_TEMPLATE_ID", ""
                ),
                "card_biz_id": f"discover_things_{task.task_id}",
                "card_data": json.dumps(
                    {"click": "", **task.to_card_data(), "session_url": session_url}
                ),
                "session_url": session_url,
            },
        )
        msg_id = self._notify_sender.send(message)
        if msg_id:
            logger.info(
                "[task_discovery] notification sent for task %s (msg_id=%s)",
                task.task_id,
                msg_id,
            )
            return True
        else:
            logger.warning(
                "[task_discovery] notification send returned None for task %s",
                task.task_id,
            )
            return False

    def _send_work_order_event(
        self,
        task: DiscoveredTask,
        user_id: str,
        session: DiscoverySession,
    ) -> bool:
        """通过 WorkOrderService 投递一条 NOTICE 工单事件，落工单通知收件箱。

        与 ``_send_notification``（外发卡片）并存：把"发现到的待确认任务"作为一条
        NOTICE 通知写入 ``ac_work_order_notification``，供工单/通知中心展示。
        ``work_order_service`` 未注入时直接返回 False（no-op，保持向后兼容）。
        失败不抛异常 — 仅记 warning 并返回 False，不影响发现主流程。
        """
        svc = self._work_order_service
        if svc is None:
            return False
        try:
            result = svc.create_work_order_event(
                event_category=NotificationCategory.NOTICE,
                biz_type="task_discovery",
                biz_id=task.task_id,
                event_type=WorkOrderEventType.TASK_DISCOVERED.value,
                # NOTICE 约束：applicant 必须为 None，否则 400201
                applicant_user_id=None,
                approver_user_ids=[],
                recipient_user_ids=[user_id],
                title=task.title,
                content={
                    **task.to_card_data(),
                    "session_url": session.session_url,
                    "task_id": task.task_id,
                },
                apply_reason=None,
                biz_data={
                    "task_id": task.task_id,
                    "bot_id": task.bot_id,
                    "owner_id": task.owner_id,
                    "session_id": session.session_id,
                    "session_url": session.session_url,
                },
                actor_id=user_id,
            )
            logger.info(
                "[task_discovery] work-order event created for task %s "
                "(notification_ids=%s, work_order_id=%s)",
                task.task_id,
                getattr(result, "notification_ids", None),
                getattr(result, "work_order_id", None),
            )
            return True
        except Exception as exc:
            logger.warning(
                "[task_discovery] work-order event failed for task %s: %s",
                task.task_id,
                exc,
            )
            return False

    def get_discovery_result(self, task_id: str) -> DiscoveryResult | None:
        """返回某个 task 的最近发现结果（含 session_id/session_url），供 status 接口查询。

        从内存 ``self._discoveries`` 读取 — 后端重启后会丢，仅反映进程内最近的 discover 结果。
        """
        return self._discoveries.get(task_id)


__all__ = [
    "DiscoveryService",
    "DiscoveryResult",
]