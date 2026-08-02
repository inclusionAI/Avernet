"""全生命周期 NodeType-aware tick(plan §3.1/§7.2/§12A,tasks T-16..T-20)。

从 ``task_scheduler.py`` 抽出以守 architecture 1000-line cap。混入
:class:`TaskScheduler`,为唯一 tick 链路(``_tick``)。

两维度(plan §2):Node = 动作维度(tick 推进),State = Task/SubTask 实体维度
(动作 fold 驱动 status)。搜推先行(FR-GRAPH-14):BOT_SEARCH 先行,命中→DISPATCH,
未匹配→DECOMPOSITION(depth+1),depth≥MAX→MARK_HANG(非直升 BBS)。exec-aggregate
触发(O-8):tick 检测父 subtask 子全 DONE → 落 EXEC_AGGREGATE 节点 + aggregate_verdict
fold。三判定节点(EXEC_ACCEPT/EXEC_AGGREGATE/GOAL_VERIFY)的判验结果由 skill 经
``on_event`` 回投驱动;tick 只推进动作拓扑 + 检测聚合触发 + hang/bbs/终验。
"""
from __future__ import annotations

from typing import Optional

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    AttemptOutcome,
    GraphStatus,
    Node,
    NodeStatus,
    NodeType,
    RunMode,
    SubTaskSpec,
    Task,
    TaskState,
    TaskStatus,
)
from agentclaw.community.core.task.protocols import aggregate_verdict
from agentclaw.community.core.task.domain.state_machine import IllegalTransitionError
from agentclaw.community.log import get_logger

logger = get_logger()

# 递归深度上限(plan §11):children depth≥MAX → MARK_HANG(等人确认,非直升 BBS)。
MAX_RECURSION_DEPTH = 3

# 同执行方 inline 重派上限(T-13,plan §18.1-12):NODE_FAILED → attempts<max 由 tick
# re-claim+fire 同执行方重派;到 max → 派 reroute 判定给失败方 exec-bot skill。
DEFAULT_MAX_ATTEMPTS = 2


class SchedulerOpsMixin:
    """v2 NodeType-aware tick。宿主须提供 ``self._svc``/``self._discover``/
    ``self._driver``/``self._decomposer``/``self._execution``(同 TaskScheduler)。"""

    # 声明宿主依赖(类型提示;实际由 TaskScheduler 提供)
    _svc: "object"
    _discover: "object"
    _driver: "object"
    _decomposer: "object"
    _execution: "object"

    # --- 图拓扑工具 ---------------------------------------------------------

    def _children(self, task: Task, node_id: str) -> list[str]:
        """直接子节点(DEPENDENCY 出边 to_node)。"""
        g = task.execution_graph
        if g is None:
            return []
        return [e.to_node for e in g.edges if e.from_node == node_id]

    def _descendants(self, task: Task, node_id: str) -> list[str]:
        """全部后代(BFS,沿出边)。"""
        g = task.execution_graph
        if g is None:
            return []
        # build adjacency
        adj: dict[str, list[str]] = {}
        for e in g.edges:
            adj.setdefault(e.from_node, []).append(e.to_node)
        seen: set[str] = set()
        stack = list(adj.get(node_id, []))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        return list(seen)

    def _subtask_depth(self, task: Task, node_id: str) -> int:
        st = task.execution_graph.state.subtasks.get(node_id)  # type: ignore[union-attr]
        return st.depth if st is not None else 0

    def _set_done(self, task: Task, node_id: str) -> Task:
        """动作节点 DONE(Node.status)+ 实体 SubtaskState.status=DONE(若分区存在)。

        重读+存:add_node 等内部已 get_by_id+save,本地 ``task`` 过期;此处重读避免
        覆盖刚落的子节点。返回重读后的 task 供调用方续用。"""
        fresh = self._svc.get(task.id)
        node = self._svc._find_node(fresh, node_id)  # noqa: SLF001
        if node is not None and node.status is not NodeStatus.DONE:
            node.status = NodeStatus.DONE
        st = fresh.execution_graph.state.subtasks.get(node_id)  # type: ignore[union-attr]
        if st is not None:
            st.status = NodeStatus.DONE
        self._svc._task_repo.save(fresh)  # noqa: SLF001
        return fresh

    def _add_child(
        self,
        task: Task,
        parent_id: str,
        node_id: str,
        spec: str,
        node_type: NodeType,
        executor: str = "",
        depth: Optional[int] = None,
    ) -> Node:
        sub = SubTaskSpec(node_id=node_id, spec=spec, run_mode=RunMode.SINGLE_BOT)
        if depth is not None:
            sub.depth = depth
        return self._svc.add_node(task.id, sub, parent_id, node_type, executor=executor)

    # --- 主 tick ------------------------------------------------------------

    def _tick(self, task: Task) -> dict:
        """NodeType-aware 推进。返回与旧 tick 同形的 action dict。"""
        task_id = task.id
        progressed = False
        g = task.execution_graph
        if g is None:
            return {"task_id": task_id, "action": "noop", "reason": "no_graph"}

        for n in list(g.nodes):
            if n.status is NodeStatus.PENDING:
                if not self._unlocked(task, n.node_id):
                    continue
                acted = self._advance_node(task, n)
            elif n.status is NodeStatus.FAILED and n.node_type is NodeType.DISPATCH:
                acted = self._retry_failed(task, n)  # tick 驱动重试/重路由(T-13)
            else:
                continue
            if acted:
                progressed = True
                # 每次推进后重读图(add_node 内部 get_by_id+save,本地 task 过期)
                task = self._svc.get(task_id)
                g = task.execution_graph
                if g is None:
                    break

        # O-8:exec-aggregate 触发 — 父 subtask 子全 DONE → 落 EXEC_AGGREGATE 节点 + fold
        task = self._svc.get(task_id)
        if self._detect_and_aggregate(task):
            progressed = True

        task = self._svc.get(task_id)
        # task 终态判定:root 聚合 DONE → GOAL_VERIFY
        if self._maybe_goal_verify(task):
            progressed = True

        task = self._svc.get(task_id)
        return {
            "task_id": task_id,
            "action": "ticked",
            "progressed": progressed,
            "graph_status": task.execution_graph.graph_status.value if task.execution_graph else "",
        }

    def _unlocked(self, task: Task, node_id: str) -> bool:
        """前驱全 DONE/SKIPPED(无依赖者恒解锁)。"""
        g = task.execution_graph
        if g is None:
            return False
        preds = [e.from_node for e in g.edges if e.to_node == node_id]
        if not preds:
            return True
        pred_status = {n.node_id: n.status for n in g.nodes}
        return all(pred_status.get(p) in (NodeStatus.DONE, NodeStatus.SKIPPED) for p in preds)

    # --- 单节点动作推进 -----------------------------------------------------

    def _advance_node(self, task: Task, n: Node) -> bool:
        nt = n.node_type
        if nt in (NodeType.RECOGNITION, NodeType.CLARIFY, NodeType.EXECUTE_START):
            # 规划阶段动作节点(skill 已在 task 创建/澄清时跑过):tick 标 DONE 推进链
            self._set_done(task, n.node_id)
            return True
        if nt is NodeType.BOT_SEARCH:
            return self._bot_search(task, n)
        if nt is NodeType.DECOMPOSITION:
            return self._decomposition(task, n)
        if nt is NodeType.DISPATCH:
            return self._dispatch(task, n)
        if nt is NodeType.MARK_HANG:
            return self._mark_hang(task, n)
        if nt is NodeType.BBS_DISPATCH:
            return self._bbs_dispatch(task, n)
        # 判定节点(EXEC_ACCEPT/EXEC_AGGREGATE/GOAL_VERIFY)由 skill on_event 回投驱动,
        # tick 中若仍 PENDING 表示等待判验 → 不推进。
        return False

    def _bot_search(self, task: Task, n: Node) -> bool:
        """搜推先行(FR-GRAPH-14):先搜推;命中→DISPATCH 子;未匹配→
        depth≥MAX 则 MARK_HANG,否则 DECOMPOSITION 子。"""
        rec = self._discover.recommend(task.id, n.node_id)
        if rec.candidates:
            lead = rec.candidates[0].bot_id
            self._add_child(
                task, n.node_id, f"{n.node_id}_disp", f"dispatch→{lead}",
                NodeType.DISPATCH, executor=lead,
            )
            self._set_done(task, n.node_id)
            logger.info("[SchedulerV2] bot-search hit node=%s → dispatch %s", n.node_id, lead)
            return True
        # 未匹配
        depth = self._subtask_depth(task, n.node_id)
        if depth >= MAX_RECURSION_DEPTH:
            hang_id = f"{n.node_id}_hang"
            self._add_child(task, n.node_id, hang_id, "mark-hang", NodeType.MARK_HANG)
            self._set_done(task, n.node_id)
            self._set_done(task, hang_id)  # hang 动作完成(graph 已 AWAITING)→ 节点 DONE,免下 tick 重挂
            self._svc.mark_graph_status(self._svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
            logger.info("[SchedulerV2] bot-search miss node=%s depth=%d ≥MAX → mark-hang", n.node_id, depth)
            return True
        # 未匹配 → 分解:DECOMPOSITION 子节点继承父 BOT_SEARCH 的 spec(真实需求文本),
        # 让 DecomposerPort.decompose_subtasks 拿到“要分解什么”,而非字面 "decomposition"。
        self._add_child(task, n.node_id, f"{n.node_id}_dec", n.spec, NodeType.DECOMPOSITION)
        self._set_done(task, n.node_id)
        logger.info("[SchedulerV2] bot-search miss node=%s → decomposition(spec=%s)", n.node_id, n.spec)
        return True

    def _decomposition(self, task: Task, n: Node) -> bool:
        """分解:decompose_subtasks(spec, state with 父 depth)→ children BOT_SEARCH(depth+1)。

        children depth≥MAX → MARK_HANG(不落 children)。"""
        parent_depth = self._subtask_depth(task, n.node_id)
        state = TaskState(public={"__decompose_parent_depth__": parent_depth})
        subs = self._decomposer.decompose_subtasks(n.spec, state)
        if subs and all(s.depth >= MAX_RECURSION_DEPTH for s in subs):
            hang_id = f"{n.node_id}_hang"
            self._add_child(task, n.node_id, hang_id, "mark-hang", NodeType.MARK_HANG)
            self._set_done(task, n.node_id)
            self._set_done(task, hang_id)  # hang 动作完成 → 节点 DONE
            self._svc.mark_graph_status(self._svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
            logger.info("[SchedulerV2] decomposition node=%s children depth≥MAX → mark-hang", n.node_id)
            return True
        for s in subs:
            self._add_child(
                task, n.node_id, s.node_id, s.spec, NodeType.BOT_SEARCH,
                executor=s.node_id, depth=s.depth,
            )
        self._set_done(task, n.node_id)
        logger.info("[SchedulerV2] decomposition node=%s → %d children", n.node_id, len(subs))
        return True

    def _dispatch(self, task: Task, n: Node) -> bool:
        """派发:claim + fire ExecutionPort(单 bot/群);Node→RUNNING + SubtaskState→RUNNING。

        计划派发的 DISPATCH 节点(plan.sub_tasks)无 assignee → discover recommend 取
        候选 + claim lead;动作链 DISPATCH(命中时落 assignee)→ 直派。完成由 skill 经
        on_event 异步回投;此处只起跑。"""
        task_id = task.id
        rec = self._discover.recommend(task_id, n.node_id)
        if n.assignee and not rec.candidates:
            lead, run_mode, candidates = n.assignee, (n.run_mode or RunMode.SINGLE_BOT), []
        elif rec.candidates:
            lead, run_mode, candidates = rec.candidates[0].bot_id, rec.run_mode, rec.candidates
        else:
            self._driver.dispatch_node(task_id, n.node_id)
            return True
        try:
            self._svc.claim_node(task_id, n.node_id, lead)
        except Exception:  # noqa: BLE001 — claim 已被占等,跳过本 tick
            return False
        if run_mode is RunMode.COOP_GROUP and candidates:
            self._execution.coop_group(task_id, n.node_id, [c.bot_id for c in candidates])
        else:
            self._execution.dispatch_single_bot(task_id, n.node_id, lead)
        # 实体维度 fold:SubtaskState → RUNNING(与 Node.status 同步)
        fresh = self._svc.get(task_id)
        st = fresh.execution_graph.state.subtasks.get(n.node_id)  # type: ignore[union-attr]
        if st is not None and st.status is not NodeStatus.RUNNING:
            st.status = NodeStatus.RUNNING
            self._svc._task_repo.save(fresh)  # noqa: SLF001
        logger.info("[Scheduler] dispatch node=%s assignee=%s", n.node_id, lead)
        return True

    def _mark_hang(self, task: Task, n: Node) -> bool:
        self._svc.mark_graph_status(task, GraphStatus.AWAITING_HUMAN_ACCEPT)
        self._set_done(task, n.node_id)
        logger.info("[SchedulerV2] mark-hang node=%s → AWAITING_HUMAN_ACCEPT", n.node_id)
        return True

    def _bbs_dispatch(self, task: Task, n: Node) -> bool:
        self._driver.escalate_to_bbs(task.id, reason=f"bbs-dispatch node={n.node_id}")
        self._set_done(task, n.node_id)
        logger.info("[SchedulerV2] bbs-dispatch node=%s", n.node_id)
        return True

    # --- tick 驱动的失败重试/重路由(T-13,plan §18.1-12)-----------------------

    def _retry_failed(self, task: Task, n: Node) -> bool:
        """tick 驱动 FAILED-Dispatch 节点的重试 / 重路由(T-13)。

        - ``attempts < max`` → 同执行方 re-claim + fire:``claim_node`` 推进
          ``attempted_executors`` 计数 + 状态机 FAILED→RUNNING + fire ExecutionPort。
          完成仍由 skill 经 ``on_event`` 异步回投(NODE_ACCEPTED/NODE_FAILED)。
        - 到 ``max`` → 向失败方 exec-bot 派发"重路由判定请求"(``ExecutionPort.probe``,
          只派一次,guard ``__reroute_probe_sent__``):由该 bot 的 task-plan-skill 判
          是否 reroute → 发起 gap bot-search(retrieve-state 上下文)→ ``add_node``
          (BOT_SEARCH) → 后续 tick 处理(命中 dispatch / 未匹配 decomposition)。
          **reroute 是 skill 判定 + 图操作,非 scheduler 的 ``redispatch(C5)`` 规则**。"""
        node_id = n.node_id
        max_attempts = int(n.properties.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        attempts = len(n.attempted_executors)
        if attempts < max_attempts:
            last = n.attempted_executors[-1].executor_id if n.attempted_executors else (n.assignee or "")
            if not last:
                return False
            try:
                self._svc.claim_node(task.id, node_id, last)  # FAILED→RUNNING + 追加 AttemptedRecord
            except IllegalTransitionError:
                return False
            self._execution.dispatch_single_bot(task.id, node_id, last)
            self._sync_subtask_running(task.id, node_id)
            logger.info(
                "[Scheduler] retry node=%s executor=%s attempt=%d/%d",
                node_id, last, attempts + 1, max_attempts,
            )
            return True
        # 到 max → 派 reroute 判定给失败方 exec-bot skill(只派一次,免每 tick 重复派)
        if n.properties.get("__reroute_probe_sent__"):
            return False
        fresh = self._svc.get(task.id)
        fn = self._svc._find_node(fresh, node_id)  # noqa: SLF001
        if fn is None:
            return False
        fn.properties["__reroute_probe_sent__"] = True
        self._svc._task_repo.save(fresh)  # noqa: SLF001
        last = n.attempted_executors[-1].executor_id if n.attempted_executors else (n.assignee or "")
        if last:
            self._execution.probe(task.id, node_id, last)
            logger.info(
                "[Scheduler] retry-exhausted node=%s → probe skill for reroute judgment", node_id,
            )
            return True
        return False

    def _sync_subtask_running(self, task_id: str, node_id: str) -> None:
        """claim 只写 Node 维;实体维 SubtaskState 在此同步 RUNNING(与 ``_dispatch`` 一致)。"""
        fresh = self._svc.get(task_id)
        st = fresh.execution_graph.state.subtasks.get(node_id)  # type: ignore[union-attr]
        if st is not None and st.status is not NodeStatus.RUNNING:
            st.status = NodeStatus.RUNNING
            self._svc._task_repo.save(fresh)  # noqa: SLF001

    # --- exec-aggregate 触发(O-8)------------------------------------------

    def _detect_and_aggregate(self, task: Task) -> bool:
        """父 subtask(DECOMPOSITION)下派发链叶子全 DONE → EXEC_AGGREGATE fold(O-8)。

        对每个已 DONE 的 DECOMPOSITION 节点,收集其全部后代 DISPATCH 叶子;若全 DONE
        且尚未有 EXEC_AGGREGATE 子 → 落 EXEC_AGGREGATE 节点 + aggregate_verdict fold;
        PASS 则 EXEC_AGGREGATE 节点(Node+SubtaskState)DONE。
        """
        g = task.execution_graph
        if g is None:
            return False
        acted = False
        for n in list(g.nodes):
            if n.node_type is not NodeType.DECOMPOSITION:
                continue
            if n.status is not NodeStatus.DONE:
                continue
            # 已有 EXEC_AGGREGATE 子 → 已处理
            if any(
                self._svc._find_node(task, cid) is not None  # noqa: SLF001
                and self._svc._find_node(task, cid).node_type is NodeType.EXEC_AGGREGATE  # noqa: SLF001
                for cid in self._children(task, n.node_id)
            ):
                continue
            # 收集后代 DISPATCH 叶子(实际执行的派发节点)
            leaves = [
                did for did in self._descendants(task, n.node_id)
                if self._svc._find_node(task, did) is not None  # noqa: SLF001
                and self._svc._find_node(task, did).node_type is NodeType.DISPATCH  # noqa: SLF001
            ]
            if not leaves:
                continue
            if not all(
                task.execution_graph.state.subtasks.get(lid) is not None  # type: ignore[union-attr]
                and task.execution_graph.state.subtasks.get(lid).status is NodeStatus.DONE  # type: ignore[union-attr]
                for lid in leaves
            ):
                continue  # 尚有叶子未闭合
            # 全闭合 → 落 EXEC_AGGREGATE + fold(重读+存,避免覆盖)
            agg_node = self._add_child(
                task, n.node_id, f"{n.node_id}_agg", "exec-aggregate", NodeType.EXEC_AGGREGATE,
            )
            fresh = self._svc.get(task.id)
            child_results = [{"outcome": AttemptOutcome.PASS} for _ in leaves]
            parent_acs = list(n.targets_acceptance) or [
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": n.node_id})
            ]
            verdict, _unmet = aggregate_verdict(parent_acs, child_results)
            agg = self._svc._find_node(fresh, agg_node.node_id)  # noqa: SLF001
            if agg is not None and verdict is AttemptOutcome.PASS:
                agg.status = NodeStatus.DONE
            st = fresh.execution_graph.state.subtasks.get(agg_node.node_id)  # type: ignore[union-attr]
            if st is not None:
                st.status = NodeStatus.DONE if verdict is AttemptOutcome.PASS else NodeStatus.FAILED
            self._svc._task_repo.save(fresh)  # noqa: SLF001
            logger.info(
                "[SchedulerV2] exec-aggregate node=%s verdict=%s (leaves=%d)",
                agg_node.node_id, verdict.value, len(leaves),
            )
            acted = True
            task = fresh
        return acted

    # --- task 终验(GOAL_VERIFY)--------------------------------------------

    def _maybe_goal_verify(self, task: Task) -> bool:
        """root 执行链全闭合(无 PENDING/RUNNING 且 root subtask DONE)→ goal-verify。

        PASS → graph VERIFIED + Task DONE;FAIL → BBS 前回 gap(graph ON_PLAZA→AWAITING_HUMAN_ACCEPT)/
        BBS 后 FAILED(此处仅落 PASS 终态;FAIL 路径由 on_event GOAL_REJECTED 驱动,见 §13)。"""
        g = task.execution_graph
        if g is None:
            return False
        if g.graph_status is GraphStatus.VERIFIED:
            return False
        # 仅 ON_PLAZA(活跃执行)可终验;AWAITING_HUMAN_ACCEPT(hang/BBS 等人确认)不终验。
        if g.graph_status is not GraphStatus.ON_PLAZA:
            return False
        # 仍有未闭合动作节点(PENDING/RUNNING)或有未解决 FAILED 节点 → 不终验。
        # FAILED 节点表示失败待 tick 重派/skill 判 reroute,未解决前不能判整图 DONE
        # (否则 goal-verify 用聚合 verdict 会在 FAILED 叶子仍在时误判 PASS 终态)。
        if any(
            n.status in (NodeStatus.PENDING, NodeStatus.RUNNING, NodeStatus.FAILED)
            for n in g.nodes
        ):
            return False
        if task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False
        # root subtask(depth=0 的 DISPATCH/BOT_SEARCH 链)产出验收
        acs = list(task.spec.goal.acceptances) if task.spec.goal else []
        verdict, _unmet = aggregate_verdict(acs, [{"outcome": AttemptOutcome.PASS}])
        if verdict is not AttemptOutcome.PASS:
            return False  # FAIL 由 on_event 回投驱动,不在 tick 内硬落
        # EXECUTING → REVIEWING(进终验)→ DONE;graph ON_PLAZA → VERIFIED
        self._advance(task, TaskStatus.REVIEWING)
        self._svc.mark_graph_status(task, GraphStatus.VERIFIED)
        self._svc.mark_terminal(task, TaskStatus.DONE)
        logger.info("[SchedulerV2] goal-verify PASS task=%s → DONE/VERIFIED", task.id)
        return True


__all__ = ["SchedulerOpsMixin", "MAX_RECURSION_DEPTH", "DEFAULT_MAX_ATTEMPTS"]
