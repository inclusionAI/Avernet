"""BBS lease 到期分支单测(对齐 task-10 brief / FR-EXT-06)。

bbs_mode 任务的 scoped RUNNING 节点超 SLA(owner bot 崩溃/挂起)→ harness:
① 直接写图把 scoped 节点标终态 FAILED(acceptance FAIL gaps=["bbs_lease_expired"]);
② 直接写图清根 bbs_owner(root node_id == task_id);
③ 不走 on_harness_fn 的 RUNNING→PENDING 重派(continue 跳过 resets 追加)。

直写图 vs on_harness_fn 的区分:on_harness_fn=编排核 on_harness 会复位 RUNNING→PENDING 重派,
与"标终态不重派"语义相反;故 bbs 到期分支绕过 on_harness_fn,由 harness 直驱图写口。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskGraphPatch,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_harness.harness import TaskHarness


# ----- 复用 test_harness.py 的 helper 形态 -----
def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


class _Clock:
    """可手动推进的时钟(单测定确定性)。"""

    def __init__(self, start: float = 0.0):
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


class _Recorder:
    """记录经 on_harness_fn 扇出的 patch(测试断言用)。"""

    def __init__(self):
        self.patches: list[TaskNodePatch] = []

    def __call__(self, patch: TaskNodePatch):
        self.patches.append(patch)


def _make_bbs_running_graph(svc: TaskGraphService, task_id: str = "t1",
                            scoped_id: str = "c1", owner: str = "botA"):
    """构造 bbs_mode 任务:根 bbs_owner=owner + 一个 RUNNING 的 run_mode='bbs' scoped 节点。"""
    graph = svc.initialize_graph(_task_info(task_id))
    svc.update_task_graph_info(task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
    # 根 bbs_owner(直驱 fold;不翻态,root node_id == task_id)
    svc.update_task_node_info(_patch(task_id, task_id, extend_props_patch={"bbs_owner": owner}))
    # 挂一个 scoped bbs 叶节点并派发为 RUNNING
    svc.add_task_nodes([_child(scoped_id, task_id)], parent_node_id=task_id)
    svc.update_task_node_info(_patch(task_id, scoped_id, status=Status.RUNNING,
                                     run_mode="bbs", assignee=owner))
    return graph


class TestBbsLeaseExpire:
    def test_bbs_lease_expire_clears_owner_and_marks_terminal_not_redispatch(self):
        """bbs RUNNING 节点超 SLA → 根 bbs_owner 清空 + scoped 节点 DONE;不重派(非 PENDING)。"""
        svc = TaskGraphService()
        graph = _make_bbs_running_graph(svc, "t1", "c1", owner="botA")

        clock = _Clock(0.0)
        rec = _Recorder()
        h = TaskHarness(svc, rec, clock=clock, sleep=lambda *_: None,
                        default_sla_timeout=10.0, default_pending_timeout=10.0, interval=0)
        h.register("t1")

        h._poll_once()        # t=0:首见 RUNNING bbs 节点 → 记时 t0=0(本轮不判)
        clock.advance(11.0)   # t=11 > sla=10 → lease 到期
        h._poll_once()        # bbs 到期分支:直写图标 FAILED + 清根 owner,continue(不追加 PENDING reset)

        # (a) 根 bbs_owner 被清空
        root = svc._get_node(graph, "t1")
        assert root.run_info.extend_props.get("bbs_owner") is None

        # (b) scoped 节点标终态 DONE(acceptance FAIL gaps=bbs_lease_expired),非 PENDING 重派
        scoped = svc._get_node(graph, "c1")
        assert scoped.status == Status.DONE
        assert scoped.run_info.acceptance_result is not None
        assert scoped.run_info.acceptance_result.verdict == AcceptanceVerdict.FAILED
        assert scoped.run_info.acceptance_result.gaps == ["bbs_lease_expired"]

        # (c) on_harness_fn 对 bbs 节点零扇出(任何 scan 都不重派 bbs 节点):
        #     RUNNING-scan 直写图 + continue 跳过 PENDING reset;FAILED-scan guard 跳过 bbs。
        #     故 rec 不应含 bbs scoped 节点("c1")的任何 patch(既非 status=PENDING,亦非 exec_error)。
        assert not any(p.node_id == "c1" for p in rec.patches), (
            f"bbs 节点不应经 on_harness_fn 扇出任何 patch(不重派): {rec.patches}")

    def test_bbs_bot_reported_fail_not_redispatched(self):
        """bbs 节点 bot 自报 FAIL(acceptance FAIL)→ DONE,FAILED-scan guard 跳过 bbs 且 on_harness 不重派。

        覆盖 FAILED-scan 的 bbs 跳过对 bot-FAIL 同样生效(非仅 lease-expire 直写图路径)。
        """
        svc = TaskGraphService()
        graph = _make_bbs_running_graph(svc, "t3", "c3", owner="botA")
        # 模拟 bot 回投验收 FAIL:RUNNING→DONE(via acceptance_result,走 _ACCEPTANCE_TRANSITIONS)
        svc.update_task_node_info(_patch(
            "t3", "c3",
            acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["bot_reported_gap"])))
        assert svc._get_node(graph, "c3").status == Status.DONE

        clock = _Clock(0.0)
        rec = _Recorder()
        h = TaskHarness(svc, rec, clock=clock, sleep=lambda *_: None, interval=0)
        h.register("t3")
        h._poll_once()  # FAILED-scan:已 FAILED 的 bbs 节点应被 guard 跳过,不扇出到 on_harness_fn

        # (a) bbs 节点仍 DONE(终态,未被 FAILED-scan 重派为 PENDING)
        assert svc._get_node(graph, "c3").status == Status.DONE
        # (b) on_harness_fn 对 bbs 节点零扇出
        assert not any(p.node_id == "c3" for p in rec.patches), (
            f"bbs bot-FAIL 节点不应经 on_harness_fn 扇出(不重派): {rec.patches}")

    def test_non_bbs_running_timeout_still_redispatches(self):
        """回归护栏:非 bbs RUNNING 超 SLA 仍走原 RUNNING→PENDING 重派(bbs 分支不影响普通节点)。"""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t2"))
        # 非 bbs 普通叶子:run_mode=single_bot
        svc.add_task_nodes([_child("c2", "t2")], parent_node_id="t2")
        svc.update_task_node_info(_patch("t2", "c2", status=Status.RUNNING,
                                         run_mode="single_bot", assignee="bot1"))

        clock = _Clock(0.0)
        rec = _Recorder()
        h = TaskHarness(svc, rec, clock=clock, sleep=lambda *_: None, default_sla_timeout=10.0, interval=0)
        h.register("t2")
        h._poll_once()        # t=0 首见记时
        clock.advance(11.0)   # t=11 > sla=10
        resets = h._poll_once()

        # 非 bbs:走原路 → 追加 status=PENDING 复位 patch 并经 on_harness_fn 扇出
        assert any(p.node_id == "c2" and p.status == Status.PENDING for p in resets)
        assert any(p.node_id == "c2" and p.status == Status.PENDING for p in rec.patches)
        # 非 bbs 节点非标终态(未被 acceptance 翻 FAILED);保持原 RUNNING 直至 on_harness 复位
        assert svc._get_node(graph, "c2").status != Status.FAILED
