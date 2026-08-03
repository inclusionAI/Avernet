"""v2 图操作 + State 写口/读口/快照 mixin(plan §4.3/§7.1/§8,tasks T-11..T-15)。

从 ``task_service.py`` 抽出以守 architecture 1000-line cap。混入 :class:`RealTaskService`。
所有图变更经此写口:fold → save;事件 append 由 ``on_event`` 路径统一收口(§8.1)。
"""
from __future__ import annotations

from typing import Optional

from agentclaw.community.core.task.domain.models import (
    Edge,
    EdgeKind,
    GraphSnapshot,
    Node,
    NodeStatus,
    NodeType,
    RunMode,
    StateSemantics,
    SubTaskSpec,
    SubtaskState,
)
from agentclaw.community.core.task.domain.events import EventKind
from agentclaw.community.core.task.domain.state_machine import IllegalTransitionError


class GraphStateOpsMixin:
    """v2 graph-operation write face + State fold + retrieve + snapshot。

    宿主类须提供 ``self._task_repo``(:class:`TaskRepo`)。``_render_kind`` 为副屏
    渲染分类(exec/control-gate/system-bridge,plan §9/O-P5)。"""

    # 声明宿主依赖(仅类型提示;实际由 RealTaskService 提供)
    _task_repo: "object"

    def _ensure_subtask_state(self, task, node_id: str) -> SubtaskState:
        assert task.execution_graph is not None
        st = task.execution_graph.state.subtasks.get(node_id)
        if st is None:
            st = SubtaskState(node_id=node_id)
            task.execution_graph.state.subtasks[node_id] = st
        return st

    @staticmethod
    def _render_kind(node_type: NodeType) -> str:
        if node_type in (NodeType.EXEC_ACCEPT, NodeType.EXEC_AGGREGATE, NodeType.GOAL_VERIFY):
            return "control-gate"
        if node_type in (
            NodeType.EXECUTE_START,
            NodeType.DISPATCH,
        ):
            return "system-bridge"
        return "exec"

    def add_node(
        self,
        task_id: str,
        node,  # Node | SubTaskSpec
        parent_node: Optional[str],
        node_type: NodeType,
        executor: str = "",
    ) -> Node:
        """落图:add_node + (parent_node 非 None 时)add_edge + 建 SubtaskState 分区。"""
        task = self._task_repo.get_by_id(task_id)
        if task.execution_graph is None:
            raise IllegalTransitionError("graph not initialized")
        if isinstance(node, SubTaskSpec):
            new_node = Node(
                node_id=node.node_id,
                spec=node.spec,
                node_type=node_type,
                run_mode=node.run_mode,
            )
            spec_depth = node.depth
        else:
            new_node = node
            new_node.node_type = node_type
            spec_depth = 0
        if executor:
            new_node.assignee = executor
        task.execution_graph.nodes.append(new_node)
        st = self._ensure_subtask_state(task, new_node.node_id)
        # plan §11:SubTaskSpec.depth(递归深度)落 SubtaskState.depth(实体维度)。
        # 根 subtask 为 0;递归 children = 父 + 1(decompose_subtasks 已算)。
        if spec_depth:
            st.depth = spec_depth
        if parent_node:
            task.execution_graph.edges.append(
                Edge(
                    edge_id=f"e-{parent_node}-{new_node.node_id}",
                    from_node=parent_node,
                    to_node=new_node.node_id,
                    kind=EdgeKind.DEPENDENCY,
                )
            )
        self._task_repo.save(task)
        return new_node

    def add_edge(self, task_id: str, from_node: str, to_node: str, kind: EdgeKind) -> Edge:
        task = self._task_repo.get_by_id(task_id)
        if task.execution_graph is None:
            raise IllegalTransitionError("graph not initialized")
        edge = Edge(edge_id=f"e-{from_node}-{to_node}", from_node=from_node, to_node=to_node, kind=kind)
        task.execution_graph.edges.append(edge)
        self._task_repo.save(task)
        return edge

    def open_reroute_search(
        self,
        task_id: str,
        failed_node_id: str,
        gap_spec: str,
    ) -> Node:
        """失败方 exec-bot skill 判定需重路由后,发起 gap bot-search(FR-GRAPH-08/T-28)。

        在失败节点的**父节点**下挂一个 BOT_SEARCH **兄弟**节点(spec=gap_spec,depth
        =失败节点同层),供后续 ``tick._bot_search`` 处理:命中 → dispatch 重派(新执行
        方)/ 未匹配 → decomposition 递归拆解(depth+1)。

        失败节点本身保留 FAILED 作历史,**不**作新节点的父 —— FAILED 不在 DONE/SKIPPED,
        作父会因 ``_unlocked`` 前驱检查锁住子节点,使 tick 永远推不进去。挂兄弟(父为失败
        节点的父,已 DONE)则新节点前驱解锁,tick 可正常推进。

        发起 reroute 时把原失败节点标 **superseded**(FAILED→DONE,状态机合法):该失败
        尝试已被 reroute 接管/闭合,不再挡 ``_maybe_goal_verify`` 的"有 FAILED 不终验"guard,
        这样 reroute 成功后全图 DONE 能正常终验;实体维 SubtaskState 保留 FAILED 作失败历史。

        gap 上下文由调用方(skill)从 ``retrieve_state(failed_node_id)`` 的 gap_records
        组装进 ``gap_spec``;本方法只负责落图。"""
        task = self._task_repo.get_by_id(task_id)
        if task.execution_graph is None:
            raise IllegalTransitionError("graph not initialized")
        # 沿入边找失败节点的父(reroute 作兄弟,避免 FAILED 父锁子)。
        parent: Optional[str] = None
        for e in task.execution_graph.edges:
            if e.to_node == failed_node_id:
                parent = e.from_node
                break
        # reroute 与失败节点同层(继承其 subtask 深度);miss→decompose 时 children=父+1。
        failed_st = task.execution_graph.state.subtasks.get(failed_node_id)
        depth = failed_st.depth if failed_st is not None else 0
        sub = SubTaskSpec(
            node_id=f"{failed_node_id}_reroute",
            spec=gap_spec,
            run_mode=RunMode.SINGLE_BOT,
            depth=depth,
        )
        new_node = self.add_node(task_id, sub, parent, NodeType.BOT_SEARCH)
        # 原失败尝试已被 reroute 接管 → 标 superseded(FAILED→DONE),免常驻 FAILED 挡终验。
        fresh = self._task_repo.get_by_id(task_id)
        assert fresh.execution_graph is not None
        for nd in fresh.execution_graph.nodes:
            if nd.node_id == failed_node_id and nd.status is NodeStatus.FAILED:
                nd.status = NodeStatus.DONE
                break
        self._task_repo.save(fresh)
        return new_node

    def update_state(
        self,
        task_id: str,
        scope: Optional[str],
        patch: dict,
        semantics: StateSemantics,
    ) -> None:
        """State 写口(plan §3.2/§8.2/§8)。经 ``on_event`` 记一条 ``STATE_UPDATED`` 事件
        (scope/patch/semantics 入 payload)→ 统一走"guard→fold→append 日志→save"写口,
        使 State 变更进事件流、可被 ``GraphCheckpoint.replay`` 还原(事件溯源,§286)。
        scope=None → public;else → subtasks[scope]。"""
        self.on_event({  # type: ignore[attr-defined]
            "task_id": task_id,
            "kind": EventKind.STATE_UPDATED.value,
            "payload": {
                "scope": scope,
                "patch": dict(patch),
                "semantics": semantics.value,
            },
        })

    def _fold_state(self, task, scope: Optional[str], patch: dict, semantics: StateSemantics) -> None:
        assert task.execution_graph is not None
        state = task.execution_graph.state
        if scope is None:
            target = state.public
        else:
            target = self._ensure_subtask_state(task, scope).__dict__
        if semantics is StateSemantics.MERGE:
            for k, v in patch.items():
                if isinstance(v, dict) and isinstance(target.get(k), dict):
                    target[k] = {**target[k], **v}
                else:
                    target[k] = v
        elif semantics is StateSemantics.APPEND:
            for k, v in patch.items():
                cur = target.get(k)
                if isinstance(v, list) and isinstance(cur, list):
                    if k == "artifacts" and all(hasattr(x, "name") for x in v):
                        by_name = {a.name: a for a in cur}
                        for a in v:
                            by_name[a.name] = a
                        target[k] = list(by_name.values())
                    else:
                        target[k] = cur + v
                else:
                    target[k] = v
        else:  # OVERWRITE
            for k, v in patch.items():
                target[k] = v

    def retrieve_state(self, task_id: str, scope: Optional[str]) -> dict:
        task = self._task_repo.get_by_id(task_id)
        if task.execution_graph is None:
            return {}
        state = task.execution_graph.state
        if scope is None:
            return {"scope": "public", "public": dict(state.public)}
        st = state.subtasks.get(scope)
        if st is None:
            return {"scope": scope, "public": dict(state.public), "subtask": None}
        return {
            "scope": scope,
            "public": dict(state.public),
            "subtask": {
                "node_id": st.node_id,
                "status": st.status.value,
                "depth": st.depth,
                "execution_context": dict(st.execution_context),
                "intermediate_results": list(st.intermediate_results),
                "artifacts": [
                    {"name": a.name, "location": a.location, "type": a.type} for a in st.artifacts
                ],
                "gap_records": [
                    {"node_id": g.node_id, "round": g.round, "unmet_criteria": list(g.unmet_criteria)}
                    for g in st.gap_records
                ],
            },
        }

    def snapshot(self, task_id: str) -> GraphSnapshot:
        task = self._task_repo.get_by_id(task_id)
        if task.execution_graph is None:
            raise IllegalTransitionError("graph not initialized")
        latest = self._event_repo.latest_seq(task_id)  # type: ignore[attr-defined]
        return GraphSnapshot(
            task_id=task_id,
            at_seq=(latest or 0),
            graph=task.execution_graph,
            taken_at="",
        )


__all__ = ["GraphStateOpsMixin"]