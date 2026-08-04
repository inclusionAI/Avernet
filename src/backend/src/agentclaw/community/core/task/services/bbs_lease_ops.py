"""BBS 自主接单 claim/lease/release lifecycle mixin(plan §10.3/§10.4,Task 3)。

从 ``task_service.py`` 抽出以守 architecture 1000-line cap(镜像
``GraphStateOpsMixin`` 的抽出模式)。混入 :class:`TaskService`,提供 BBS 节点
认领(``claim_node``)、主动让出(``release_node``)、兜底租期到期收回
(``expire_lease``)三个写口。纯搬迁,零行为变更。

宿主类须提供 ``self._load`` / ``self._find_node`` / ``self._emit`` /
``self._task_repo`` / ``self._attempt_record``(均由 ``TaskService`` 提供)。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.domain.events import EventKind
from agentclaw.community.core.task.domain.models import (
    NodeStatus,
    RunMode,
    Task,
)
from agentclaw.community.core.task.domain.repository import TaskNotFoundError
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
    require_node_transition,
)
from agentclaw.community.core.task.protocols import DispatchResult
from agentclaw.community.log import get_logger

logger = get_logger()


# --- helpers ----------------------------------------------------------------


BBS_LEASE_FALLBACK_SECONDS: int = 3600  # 兜底租期(全局,非 bot 预测);崩溃检出延迟上界。取值评审项(spec §7.2)


def _utcnow() -> datetime:
    """UTC now seam — tests monkeypatch this to control lease expiry."""
    return datetime.now(timezone.utc)


def _new_accept_token() -> str:
    return "tok-" + uuid.uuid4().hex[:12]


class BbsLeaseOpsMixin:
    """BBS 节点 claim/lease/release 写口(§10.3/§10.4)。

    宿主类须提供 ``self._load`` / ``self._find_node`` / ``self._emit`` /
    ``self._task_repo`` / ``self._attempt_record``(均由 :class:`TaskService` 提供)。
    """

    def claim_node(
        self,
        task_id: str,
        node_id: str,
        executor_id: str,
        run_mode: Optional[RunMode] = None,
    ) -> Optional[DispatchResult]:
        """CAS: PENDING → RUNNING + assignee + record the attempt. Raises if
        the node is already claimed or terminal.

        ``run_mode``(可选,BBS 自主接单传入)写入 ``node.run_mode``;未传时保留
        节点已有 mode,再退 ``SINGLE_BOT``。系统按全局 ``BBS_LEASE_FALLBACK_SECONDS``
        算兜底 ``lease_until``(非 bot 预测,仅为 sweeper 崩溃检出兜底),存
        ``node.properties["lease_until"]`` 并随 :class:`DispatchResult` 返回。"""
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            raise TaskNotFoundError(f"node {node_id} not in task {task_id}")
        require_node_transition(node.status, NodeStatus.RUNNING)
        node.status = NodeStatus.RUNNING
        node.assignee = executor_id
        node.run_mode = run_mode or node.run_mode or RunMode.SINGLE_BOT
        lease_iso = (_utcnow() + timedelta(seconds=BBS_LEASE_FALLBACK_SECONDS)).isoformat()
        node.properties["lease_until"] = lease_iso
        node.attempted_executors.append(
            self._attempt_record(executor_id, node)
        )
        self._emit(
            task,
            EventKind.NODE_RUNNING,
            node_id=node_id,
            from_status=NodeStatus.PENDING.value,
        )
        token = _new_accept_token()
        self._task_repo.save(task)
        logger.info(
            "[Task] task=%s claim_node node=%s → running executor=%s run_mode=%s lease_until=%s",
            task_id, node_id, executor_id, node.run_mode.value, lease_iso,
        )
        return DispatchResult(
            node_id=node_id,
            executor_id=executor_id,
            run_mode=node.run_mode,
            accept_token=token,
            lease_until=lease_iso,
        )

    def release_node(
        self, task_id: str, node_id: str, executor_id: str
    ) -> Optional[Task]:
        """BBS 接单主动让出(§10.4):仅当前 assignee 可调;RUNNING→FAILED(outcome=handoff),
        下个 bot 立即接力。不泵 scheduler tick、不升 HUMAN(经 NODE_RELEASED fold)。"""
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            raise TaskNotFoundError(f"node {node_id} not in task {task_id}")
        if node.assignee != executor_id:
            raise Forbidden(f"only assignee {node.assignee} may release node {node_id}")
        require_node_transition(node.status, NodeStatus.FAILED)
        node.status = NodeStatus.FAILED
        node.assignee = None
        node.properties["release_outcome"] = "handoff"
        self._emit(task, EventKind.NODE_RELEASED, node_id=node_id, outcome="handoff")
        self._task_repo.save(task)
        logger.info(
            "[Task] task=%s release_node node=%s by=%s → failed(handoff)",
            task_id, node_id, executor_id,
        )
        return self._task_repo.get_by_id(task_id)

    def expire_lease(self, task_id: str, node_id: str) -> Optional[Task]:
        """兜底租期到期收回(§10.3,清扫器调):RUNNING→FAILED(outcome=lease_expired)。
        节点可能已被 bot release/complete → 此时 RUNNING→FAILED 非法,吞 IllegalTransitionError。"""
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            return None
        try:
            require_node_transition(node.status, NodeStatus.FAILED)
        except IllegalTransitionError:
            return self._task_repo.get_by_id(task_id)  # 已非 RUNNING,无需收回
        node.status = NodeStatus.FAILED
        node.assignee = None
        node.properties["release_outcome"] = "lease_expired"
        self._emit(task, EventKind.NODE_RELEASED, node_id=node_id, outcome="lease_expired")
        self._task_repo.save(task)
        logger.info(
            "[Task] task=%s expire_lease node=%s → failed(lease_expired)", task_id, node_id
        )
        return self._task_repo.get_by_id(task_id)
