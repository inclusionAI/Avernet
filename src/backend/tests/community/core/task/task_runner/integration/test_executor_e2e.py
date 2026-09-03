"""G1 PR2:三模态(single_bot/coop_group/bbs)+ 动态拉群(HIT_MULTI_BOTS) + poller 异步回投端到端 + R3 锁模型验证。

double 驱动(进程内模拟 OpenApi bot grant/send/poll + BCS create_group/session/run poll)。
验证引擎自当 ResultSink/TaskContextBuilder 的接线闭环:
  dispatch → TaskExecutor 三模态分发 → poller daemon 线程轮询终态 → 翻译 → report_result → on_report → 翻态推进
R3:poller 线程(持有自有 event loop)回投 vs on_execute 请求线程并发回投,验证 per-task threading.RLock 串行成立。
零 case 知识:节点名只出现在 planning/search double 产出(模拟 skill),框架代码不含。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, patch

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status,
    TaskInfo, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.client.double.double_bcs_client import _DoubleBcsClient
from agentclaw.community.core.task.task_runner.client.double.double_open_api_bot import _DoubleOpenApiBot
from agentclaw.community.core.task.task_runner.client.double.double_bcs_bot_identity_resolver import (
    _DoubleBcsBotIdentityResolver,
)


# ===== double:phase-aware bot (planning/search round-trip + execute poll)=====
class _PhaseBot(_DoubleOpenApiBot):
    """send_and_wait_async 用于 planning/search 同步取结果;send_message/get_run 用于 execute 异步投递+poll。

    按节点 node_id 决定 search 结果(模拟 skill 决策):
    - N_single → HIT_SINGLE(assignee=worker_single)
    - N_group  → HIT_MULTI_BOTS(manager_worker,group_name,members_info)
    - N_sm     → HIT_MULTI_BOTS(state_machine,definition_yaml)
    - N_bbs    → MISS(触发升 BBS 链路)
    其余 → HIT_SINGLE(worker_single) 兜底。
    execute 投递后 poll-to-terminal:首次 get_run RUNNING,第二次 COMPLETED,result.content = 节点产出字符串。
    """

    def __init__(self):
        super().__init__(final_status="COMPLETED", content=None, poll_once_then_terminal=True, terminal_after=1)

    async def send_and_wait_async(self, *, bot_id, message, metadata=None, timeout=180.0, poll_interval=2.0):
        phase = (metadata or {}).get("phase")
        # 从 message 里粗解析 node_id(模拟 skill 读 prompt)
        nid = _extract_node_id(message)
        if phase == "planning":
            children = _planning_children(nid)
            return {"status": "COMPLETED", "result": {"content": json.dumps(children, ensure_ascii=False)}, "error": None}
        if phase == "search":
            return {"status": "COMPLETED", "result": {"content": json.dumps(_search_result(nid), ensure_ascii=False)}, "error": None}
        return {"status": "COMPLETED", "result": {"content": ""}, "error": None}

    async def get_run(self, run_id):
        run = await super().get_run(run_id)
        # execute 回投:把 content 设成模拟产出(worker bot 跑完的产出字符串)
        if run.get("status") == "COMPLETED":
            run["result"] = {"content": json.dumps({
                "success": True,
                "data": f"output_{run_id[:6]}",
                "gaps": [],
            }, ensure_ascii=False)}
        return run


def _extract_node_id(message: str) -> str | None:
    """从 prompt 里粗解析目标 node_id(模拟 skill 读 prompt 的目标节点)。

    planning prompt 含 ``node_id=<nid>`` 字面量;search prompt 嵌 ``"node_id": "<nid>"`` JSON。
    统一用 ``"node_id": "X"`` 或 ``node_id=X`` 两种串提取(够用且不依赖 JSON 平衡解析)。
    """
    import re
    m = re.search(r'"node_id"\s*:\s*"([^"]+)"', message)
    if m:
        return m.group(1)
    m = re.search(r"node_id=([A-Za-z0-9_]+)", message)
    if m:
        return m.group(1)
    return None


def _planning_children(target_nid: str | None) -> list:
    """模拟 planning skill 产出 List[TaskSpec](对齐返回格式)。"""
    if target_nid is None or target_nid == "t_phase":
        # 根初始规划:产三个子节点(各一种模态)
        return [
            {"metadata": {"task_id": "N_single", "title": "单bot任务", "instruction": "do single"},
             "context": {"background": "bg_single", "extend_props": {}},
             "goal": {"objective": "single obj", "acceptances": [{"id": "a_s", "description": "单bot验收"}]}},
            {"metadata": {"task_id": "N_group", "title": "协作群任务", "instruction": "do group"},
             "context": {"background": "bg_group", "extend_props": {}},
             "goal": {"objective": "group obj", "acceptances": [{"id": "a_g", "description": "协作群验收"}]}},
            {"metadata": {"task_id": "N_sm", "title": "状态机任务", "instruction": "do sm"},
             "context": {"background": "bg_sm", "extend_props": {}},
             "goal": {"objective": "sm obj", "acceptances": [{"id": "a_sm", "description": "状态机验收"}]}},
        ]
    return []  # 其它节点 gap 闭(验收通过)


def _search_result(nid: str | None) -> dict:
    if nid == "N_single":
        return {"outcome": "HIT_SINGLE", "bot_id": "worker_single"}
    if nid == "N_group":
        return {"outcome": "HIT_MULTI_BOTS", "bot_ids": ["bm", "bw"],
                "collab_mode": "manager_worker", "group_name": "G_market",
                "manager_bot_id": "bm",
                "members_info": [{"bot_id": "bm", "role": "manager", "responsibility": "r1"},
                                 {"bot_id": "bw", "role": "worker", "responsibility": "r2"}]}
    if nid == "N_sm":
        return {"outcome": "HIT_MULTI_BOTS", "bot_ids": ["bs1", "bs2"],
                "collab_mode": "state_machine", "group_name": "G_sm",
                "definition_yaml": "states: [s1, s2]", "members_info": []}
    if nid == "N_bbs":
        return {"outcome": "MISS", "miss_reason": "no matching bot"}
    return {"outcome": "HIT_SINGLE", "bot_id": "worker_single"}


# ===== discover double:语义预查候选集(返回固定候选)=====
class _PollerModeSettings:
    """Exercise the pull-poller branch; production defaults to skill HTTP push."""

    def is_enabled(self, setting_type: str) -> bool:
        return setting_type != "skill_report_enabled"


class _DiscoverStub:
    def search_by_keyword(self, **kw):
        return {"total": 2, "items": [
            {"bot_id": "worker_single", "bot_name": "WS", "bot_desc": "d", "recommend": {"score": 0.9, "short_profile": "p", "reasons": ["r"]}},
            {"bot_id": "bm", "bot_name": "BM", "bot_desc": "d", "recommend": {"score": 0.85, "short_profile": "p", "reasons": ["r"]}},
        ]}


def _task_info(task_id="t_phase"):
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="存储尽调", instruction="produce DD"),
            context=Context(background="存储行业"),
            goal=Goal(objective="产出尽调报告",
                      acceptances=[AcceptanceCriteria(id=f"ac{i}", description=f"d{i}") for i in range(1, 6)]),
        ),
        source_type="bot", owner_bot_id="owner_bot",
        execution_config={"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ===== Test 1: single_bot 投递 + poller 回投 → SUCCESS =====
class TestSingleBotPollReportE2E:
    def test_single_bot_dispatch_poll_report_done(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info())
        eng = ExecutionEngine(svc, bot=_PhaseBot(), bcs=_DoubleBcsClient(), discover=_DiscoverStub(),
                              bcs_identity=_DoubleBcsBotIdentityResolver(), task_search_skill_enabled=True,
                              task_settings=_PollerModeSettings())
        _run(eng.on_execute("t_phase"))
        g = svc.query_task_dashboard("t_phase")
        nodes = {n.node_id: n for n in g.tasks}
        # 三模态都应被派发(RUNNING);single_bot 已 patch assignee=worker_single
        assert nodes["N_single"].run_info.run_mode == "single_bot"
        assert nodes["N_single"].run_info.assignee == "worker_single"
        assert nodes["N_single"].status == Status.RUNNING
        assert nodes["N_group"].status == Status.RUNNING  # 等拉群后投递(或先 RUNNING 标记)
        # 等 poller 回投 single_bot 终态(poll-to-terminal,几秒内)
        _wait_for(lambda: svc.query_task_dashboard("t_phase").tasks[0].status in (Status.DONE, Status.HUNG) or
                  any(n.node_id == "N_single" and n.status == Status.SUCCESS for n in svc.query_task_dashboard("t_phase").tasks),
                  timeout=10.0)
        g = svc.query_task_dashboard("t_phase")
        n = next(n for n in g.tasks if n.node_id == "N_single")
        assert n.status == Status.SUCCESS, f"single_bot poll 回投未翻 SUCCESS, status={n.status}"
        assert n.run_info.output, "single_bot 产出未 fold"
        _stop_poller(eng)


# ===== Test 2: HIT_MULTI_BOTS 动态拉 manager_worker 群 + poll 回投 =====
class TestCoopGroupManagerWorkerE2E:
    def test_form_group_and_dispatch(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info())
        bcs = _DoubleBcsClient(session_status="completed", session_output={
                                   "success": True, "data": "group_out", "gaps": []},
                               poll_once_then_terminal=True, terminal_after=1)
        eng = ExecutionEngine(svc, bot=_PhaseBot(), bcs=bcs, discover=_DiscoverStub(),
                              bcs_identity=_DoubleBcsBotIdentityResolver(), task_search_skill_enabled=True,
                              task_settings=_PollerModeSettings())
        _run(eng.on_execute("t_phase"))
        g = svc.query_task_dashboard("t_phase")
        n_group = next(n for n in g.tasks if n.node_id == "N_group")
        assert n_group.run_info.run_mode == "coop_group"
        assert str(n_group.run_info.assignee).startswith("grp_"), f"assignee 非群 id: {n_group.run_info.assignee}"
        assert n_group.status == Status.RUNNING
        # 等 poller 回投 coop_group 终态
        _wait_for(lambda: next((n for n in svc.query_task_dashboard("t_phase").tasks if n.node_id == "N_group"), None) is not None and
                  next(n for n in svc.query_task_dashboard("t_phase").tasks if n.node_id == "N_group").status == Status.SUCCESS,
                  timeout=10.0)
        n_group = next(n for n in svc.query_task_dashboard("t_phase").tasks if n.node_id == "N_group")
        assert n_group.status == Status.SUCCESS, f"coop_group poll 回投未翻 SUCCESS, status={n_group.status}"
        _stop_poller(eng)


# ===== Test 2b: manager_worker 群建群内联挂 §4 event_subscriptions(execute→engine→form_coop_group→create_group) =====
class _RecordingDoubleBcsClient(_DoubleBcsClient):
    """记录所有 create_group 请求(供断言 manager_worker 群挂了 §4 订阅)。"""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.created_reqs: list = []

    async def create_group(self, req):
        self.created_reqs.append(req)
        return await super().create_group(req)


class TestManagerWorkerEventSubscriptionsE2E:
    def test_execute_manager_worker_group_attaches_event_subscriptions(self):
        """execute(on_execute)→动态派发 manager_worker 群→form_coop_group→create_group 内联挂 §4 订阅。
        鉴权走既有 Bearer(+HMAC),无 cookie(见 spec §4.3);sink.url 用 api_base_url 拼 Avernet 回调路由。"""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info())
        bcs = _RecordingDoubleBcsClient(poll_once_then_terminal=False)   # 建群即可断言,不靠终态
        eng = ExecutionEngine(svc, bot=_PhaseBot(), bcs=bcs, discover=_DiscoverStub(),
                              bcs_identity=_DoubleBcsBotIdentityResolver(),
                              api_base_url="https://api.example.com", task_search_skill_enabled=True)
        _run(eng.on_execute("t_phase"))
        _wait_for(lambda: any(r.group_strategy == "manager_worker" for r in bcs.created_reqs),
                  timeout=10.0)
        mw = next(r for r in bcs.created_reqs if r.group_strategy == "manager_worker")
        subs = mw.event_subscriptions
        assert subs and len(subs) == 1
        s = subs[0]
        assert s["name"] == "avernet-manager-worker"
        assert s["payload"] == {"mode": "full"}
        assert set(s["event_filters"]) == {
            "session.created",
            "task.assigned", "task.completed", "session.completed",   # §4(group.created 不再订阅)
        }
        assert s["sink"]["type"] == "webhook"
        assert s["sink"]["url"] == "https://api.example.com/api/v1/collaboration/tasks/callback/report"
        assert s["sink"]["request_timeout_ms"] == 10000
        # 同批 state_machine 群也内联挂 avernet-state_machine 订阅(5 个 state_machine.* 事件)
        sm_reqs = [r for r in bcs.created_reqs if r.group_strategy == "state_machine"]
        assert sm_reqs and all(r.event_subscriptions for r in sm_reqs)
        sm_sub = sm_reqs[0].event_subscriptions[0]
        assert sm_sub["name"] == "avernet-state_machine"
        assert "state_machine.run.completed" in sm_sub["event_filters"]
        _stop_poller(eng)


# ===== Test 3: state_machine 模态拉群 + poll =====
class TestCoopGroupStateMachineE2E:
    def test_state_machine_group(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info())
        bcs = _DoubleBcsClient(sm_status="completed", sm_output={
                                   "success": True, "data": "sm_out", "gaps": []},
                               poll_once_then_terminal=True, terminal_after=1)
        eng = ExecutionEngine(svc, bot=_PhaseBot(), bcs=bcs, discover=_DiscoverStub(),
                              bcs_identity=_DoubleBcsBotIdentityResolver(), task_search_skill_enabled=True,
                              task_settings=_PollerModeSettings())
        _run(eng.on_execute("t_phase"))
        g = svc.query_task_dashboard("t_phase")
        n_sm = next(n for n in g.tasks if n.node_id == "N_sm")
        assert n_sm.run_info.run_mode == "coop_group"
        assert str(n_sm.run_info.assignee).startswith("grp_")
        _wait_for(lambda: next(n for n in svc.query_task_dashboard("t_phase").tasks if n.node_id == "N_sm").status == Status.SUCCESS,
                  timeout=10.0)
        n_sm = next(n for n in svc.query_task_dashboard("t_phase").tasks if n.node_id == "N_sm")
        assert n_sm.status == Status.SUCCESS, f"state_machine poll 回投未翻 SUCCESS, status={n_sm.status}"
        _stop_poller(eng)


# ===== Test 4: BBS 统一 dispatch(执行器接单,最终状态由 BBS 回投收敛)=====
class TestBbsDispatchE2E:
    def test_bbs_dispatch_invokes_bbs_adapter(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t_bbs"))
        bcs = _DoubleBcsClient()
        exe = eng = ExecutionEngine(svc, bot=_PhaseBot(), bcs=bcs, discover=_DiscoverStub(),
                                    bcs_identity=_DoubleBcsBotIdentityResolver())
        n = TaskNode(node_id="N_bbs", task_id="t_bbs", status=Status.RUNNING,
                     task_spec=_task_info("t_bbs").task_spec,
                     run_info=RuntimeInfo(run_mode="bbs", assignee="bbs_bot"),
                     node_run_graph=None)  # type: ignore[arg-type]
        svc.add_task_nodes([n], parent_node_id="t_bbs")
        with patch(
            "agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor.notify",
            new_callable=AsyncMock,
        ) as notify:
            ok = _run(exe._executor.dispatch([n]))  # type: ignore[attr-defined]
        assert ok == [True]
        notify.assert_awaited_once()
        assert n.status == Status.RUNNING  # 最终状态仍由 BBS report 回投收敛
        _stop_poller(eng)


# ===== Test 5: R3 锁模型——poller 线程 vs 请求线程并发回投 per-task RLock 串行 =====
class TestR3LockModel:
    def test_concurrent_report_serialized(self):
        """两个回投事件(一个来自 poller 线程,一个来自请求线程)针对同一 task_id,
        per-task threading.RLock 应保证 on_report 串行执行,不竞态翻态(update_task_node_info 状态机不破)。"""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t_lock"))
        eng = ExecutionEngine(svc, bot=_PhaseBot(), bcs=_DoubleBcsClient(), discover=_DiscoverStub(),
                              bcs_identity=_DoubleBcsBotIdentityResolver())
        # 手动建两个 PENDING 子节点 + 父 PLANNING,模拟一批兄弟
        from agentclaw.community.core.task.domain.models import TaskNodePatch, AcceptanceResult, AcceptanceVerdict
        children = [
            TaskNode(node_id="L1", task_id="t_lock", status=Status.RUNNING,
                     task_spec=_task_info("t_lock").task_spec, run_info=RuntimeInfo(), node_run_graph=None),  # type: ignore[arg-type]
            TaskNode(node_id="L2", task_id="t_lock", status=Status.RUNNING,
                     task_spec=_task_info("t_lock").task_spec, run_info=RuntimeInfo(), node_run_graph=None),  # type: ignore[arg-type]
        ]
        svc.add_task_nodes(children, "t_lock")
        # 用线程并发回投两个 PASS,看是否任一抛 TaskStateError(RLock 应串行避免)
        errors: list[Exception] = []

        def _report(node_id):
            try:
                loop = asyncio.new_event_loop()
                patch = TaskNodePatch(task_id="t_lock", node_id=node_id,
                                      output_patch={"data": "ok"},
                                      acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))
                loop.run_until_complete(eng.on_report(patch))
                loop.close()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=_report, args=("L1",), name="req-thread-1")
        t2 = threading.Thread(target=_report, args=("L2",), name="req-thread-2")
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # RLock 串行:两个回投都不应抛状态机异常(并发写同 graph 被锁保护)
        assert not errors, f"并发回投抛异常(R3 锁失效): {errors}"
        g = svc.query_task_dashboard("t_lock")
        n1 = next(n for n in g.tasks if n.node_id == "L1")
        n2 = next(n for n in g.tasks if n.node_id == "L2")
        assert n1.status == Status.SUCCESS and n2.status == Status.SUCCESS, \
            f"并发回投后状态错: L1={n1.status} L2={n2.status}"
        _stop_poller(eng)


# ===== helpers =====
def _wait_for(pred, timeout=10.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if pred():
                return
        except Exception:  # noqa: BLE001  节点尚未就绪,继续等
            pass
        time.sleep(interval)
    raise AssertionError(f"等待超时({timeout}s)条件未达成")


def _stop_poller(eng: ExecutionEngine):
    if getattr(eng, "_executor", None) is not None and eng._executor._poller is not None:  # type: ignore[attr-defined]
        eng._executor._poller.stop()  # type: ignore[attr-defined]
