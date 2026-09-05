"""``POST /api/v1/collaboration/tasks/callback/report`` 单测 —— 覆盖三种回投形态。

被测对象:``adapters/http/task/router.py::report_callback``(route ``/callback/report``)
及其核心 ``_dispatch``。``report_callback`` 固定 ``disposition="result"``、``schema_cls=
TaskCallbackRequest``,把原始 body 交 ``_dispatch`` 按 body 形态分流:

  1. ClawMind(``HttpCallbackPayload`` 四字段 ``workflow_id/flow_id/status/ext_info``)→
     ``auth.verify(source="claw_mind")`` → ``translate_claw_mind`` → ``svc.callback.ingest``
     (仅落 ``task_callback`` 审计,不推进编排核);
  2. BCN manager-worker(CloudEvent,``group.created/session.created/task.assigned/
     task.completed/session.completed``)→ ``auth.verify(source="bcn")`` →
     ``svc.apply_manager_worker_event``(merge 进单 session 行;``session.completed`` 收敛);
  3. BCN state-machine(CloudEvent,``state_machine.*``)→ ``auth.verify(source="bcn")`` →
     ``translate_bcn`` + 经 BCS ``GET /state-machine-runs/{run_id}`` 取回 run 明细/DAG →
     ``ingest`` 落库;run 终态(``completed/failed/aborted``)→ ``svc.converge_by_session``
     收敛(按 ``session_id`` 反查框架节点 → ``on_report`` 翻态);
  4. 羽雀/框架节点级(``TaskCallbackRequest`` 富 schema)→ ``translate`` → ``report_result``;
  5. 兜底 ``TaskCallbackDataDTO``(``loop_task_id+result`` 旧契约)→ ``callback_from_dto`` →
     ``report_result``;非 JSON → ``HTTPException(422)``。

正确性校验两点:(1) 转换后落 ``task_callback`` 的 ``TaskCallbackRecord`` 字段全面/正确;
(2) 任务/任务图谱状态收敛正确(success→DONE / failed→FAILED)。

输入数据形态对齐语雀:
- ClawMind《ClawMind回调服务》§八 ``HttpCallbackPayload``;
- BCN《BCS Group 回调接入说明》§4 manager-worker 与 §3 state-machine。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.adapters.http.task import router
from agentclaw.community.adapters.http.task.auth import NoopCallbackAuthenticator
from agentclaw.community.adapters.http.task.translator import (
    merge_manager_worker_execution_graph,
    parse_manager_worker_bcn,
)
from agentclaw.community.core.task.task_runner.client import (
    callback_data_enricher as _enricher_mod,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import (
    TaskCallbackRecord,
    TaskNodeRunInfoRecord,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_runner.callback_correlation import (
    InMemoryCallbackCorrelationRegistry,
)

_CLAW_MIND_BODY = {
    "workflow_id": "risk-review-pipeline",
    "flow_id": "flow-abc-123",
    "status": "node_succeeded",  # 顶层仅粗粒度事件类型,取值不统一
    "ext_info": {
        "flow_runs": {
            "id": "fr1", "flow_id": "flow-abc-123", "status": "succeeded",
            "origin_session_id": "S-9",
        },
        "node_executions": [
            {"node_id": "N1", "status": "succeeded",
             "output_json": {"answer": 42}, "error_text": None},
        ],
    },
}


def _bcn_event(event_type: str, *, scope: dict | None = None, data: dict | None = None,
               event_id: str = "evt-1") -> dict:
    """构造 BCN(BCS Group)CloudEvent 信封骨架(对齐语雀《BCS Group 回调接入说明》)。"""
    return {
        "spec_version": "1.0", "event_id": event_id, "event_type": event_type,
        "schema_version": "1.0", "source": "bcs",
        "occurred_at": "2026-08-18T10:01:00.000Z",
        "recorded_at": "2026-08-18T10:01:00.005Z",
        "scope": scope or {},
        "stream": {"key": "state-machine-run:run-1", "sequence": 6},
        "actor": {"type": "bot", "id": "b1"},
        "data": data or {},
    }


# ===== 测试替身 =====

class _FakeRequest:
    """轻量 Starlette Request 替身:仅满足 ``_dispatch``/``envelope`` 所需面。

    非 ``starlette.Request`` 子类 → ``envelope_errors._find_request`` 返 ``None``,
    领域异常原样上抛(单测按 ``pytest.raises`` 断言);成功路径返回 ``Envelope``。
    """

    def __init__(self, body: bytes, *, method: str = "POST",
                 path: str = "/api/v1/collaboration/tasks/callback/report",
                 headers: dict | None = None) -> None:
        self._body = body
        self.method = method
        self.url = SimpleNamespace(path=path)
        self.headers = headers or {}
        self.state = SimpleNamespace(trace_id="")

    async def body(self) -> bytes:
        # Starlette 首次读后缓存;二次读仍得同一份(report_callback 与 _dispatch 各读一次)。
        return self._body


class _FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def _install_fake_bcs(
    monkeypatch: pytest.MonkeyPatch, *,
    run_detail: Any = None, run_status: int = 200,
    graph_detail: Any = None, graph_status: int = 200,
    raise_on_get: bool = False,
) -> None:
    """替换 ``callback_data_enricher.httpx.AsyncClient`` 为可控假客户端(打桩 enricher 短连 BCS GET),断真网。"""
    rd = run_detail if run_detail is not None else {
        "run": {"status": "completed", "output": {"final": "ok"}}, "nodes": [],
    }
    gd = graph_detail if graph_detail is not None else {
        "definition": {"name": "sm"}, "nodes": [], "edges": [],
    }

    class _Client:
        # 真实签名 ``AsyncClient(timeout=10.0)`` —— 接受并忽略构造参数。
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str) -> _FakeResp:
            if raise_on_get:
                raise RuntimeError("bcs unreachable")
            if url.endswith("/graph"):
                return _FakeResp(graph_status, gd)
            return _FakeResp(run_status, rd)

    monkeypatch.setattr(_enricher_mod.httpx, "AsyncClient", _Client)


class _RecordingEngine:
    """编排核 stub:记录 ``on_report``/``on_start`` 入参 ``TaskNodePatch``(async 签名)。"""

    def __init__(self) -> None:
        self.reports: list[TaskNodePatch] = []
        self.starts: list[TaskNodePatch] = []

    async def on_report(self, patch: TaskNodePatch) -> TaskNodePatch:
        self.reports.append(patch)
        return patch

    async def on_start(self, patch: TaskNodePatch) -> TaskNodePatch:
        self.starts.append(patch)
        return patch


class _TaskStateErrorEngine(_RecordingEngine):
    """``on_report`` 抛 ``TaskStateError``,模拟"回投到已终态节点"的框架拒绝。"""

    def __init__(self, *, terminal: bool = True) -> None:
        super().__init__()
        self._terminal = terminal

    async def on_report(self, patch: TaskNodePatch) -> TaskNodePatch:
        raise TaskStateError("node in terminal state" if self._terminal else "bad transition")


class _FakeCallbackRepo:
    """``TaskCallbackRepositoryProtocol`` 内存实现:记录所有 upsert,支持按 session 反查。"""

    def __init__(self) -> None:
        self.calls: list[TaskCallbackRecord] = []

    def upsert(self, rec: TaskCallbackRecord) -> TaskCallbackRecord:
        self.calls.append(rec)
        return rec

    def get_latest_by_session(self, main_session_id: str) -> TaskCallbackRecord | None:
        latest = None
        for rec in self.calls:
            if rec.main_session_id == main_session_id:
                latest = rec
        return latest

    def get(self, run_id: str, node_id: str) -> TaskCallbackRecord | None:
        for rec in reversed(self.calls):
            if rec.run_id == run_id and rec.node_id == node_id:
                return rec
        return None

    def find_by_event_id(self, event_id: str) -> TaskCallbackRecord | None:
        for rec in reversed(self.calls):
            if rec.event_id == event_id:
                return rec
        return None


class _FakeRunInfoRepo:
    """``TaskNodeRunInfoRepositoryProtocol`` 内存实现:按 ``session_id`` 反查框架节点。"""

    def __init__(self, by_session: dict[str, TaskNodeRunInfoRecord] | None = None) -> None:
        self._by_session = by_session or {}
        self.inserts: list[TaskNodeRunInfoRecord] = []

    def get_by_session_id(self, session_id: str) -> TaskNodeRunInfoRecord | None:
        return self._by_session.get(session_id)

    def insert(self, rec: TaskNodeRunInfoRecord) -> TaskNodeRunInfoRecord:
        self.inserts.append(rec)
        return rec


class _FakeGraph:
    """``TaskGraphService`` 替身:``query_task_dashboard`` 返回配置好的节点状态。"""

    def __init__(self, nodes_by_task: dict[str, list[TaskNode]] | None = None) -> None:
        self._nodes_by_task = nodes_by_task or {}

    def query_task_dashboard(self, task_id: str, node_id: str | None = None) -> TaskExecutionGraph:
        return TaskExecutionGraph(
            run_id=1, loop_round=0, status=Status.RUNNING,
            tasks=list(self._nodes_by_task.get(task_id, [])),
            relations=[], task_id=task_id,
        )


def _node(task_id: str, node_id: str, status: Status = Status.RUNNING) -> TaskNode:
    spec = TaskSpec(
        metadata=Metadata(task_id=task_id, title="t", instruction="i"),
        context=Context(background=""), goal=Goal(objective="", acceptances=[]),
    )
    return TaskNode(
        node_id=node_id, task_id=task_id, status=status, task_spec=spec,
        run_info=RuntimeInfo(), node_run_graph=None,  # node_run_graph 仅作引用,测试不读
    )


class _CallbackTestService(TaskService):
    """真实 ``TaskService`` + 注入式编排核:test seam 是覆写 ``_build_engine``。

    用真实 ``apply_manager_worker_event``/``converge_by_session``/``get_task_dashboard``,
    配合内存 repo 与 ``_FakeGraph``,可端到端观察"回投 → 转换 → 落库 → adapt → on_report 翻态"。
    """

    def __init__(self, *, graph: _FakeGraph, engine: _RecordingEngine,
                 callback_repo: _FakeCallbackRepo, run_info_repo: _FakeRunInfoRepo) -> None:
        self._test_engine = engine
        super().__init__(
            graph, callback_repo=callback_repo, task_node_run_info_repo=run_info_repo,
        )

    def _build_engine(self, **kw):  # noqa: D401, ANN202
        return self._test_engine


def _make_svc(*, engine: _RecordingEngine | None = None,
              graph_nodes: dict[str, list[TaskNode]] | None = None,
              run_info_by_session: dict[str, TaskNodeRunInfoRecord] | None = None,
              ) -> tuple[_CallbackTestService, _RecordingEngine, _FakeCallbackRepo, _FakeRunInfoRepo]:
    engine = engine or _RecordingEngine()
    callback_repo = _FakeCallbackRepo()
    run_info_repo = _FakeRunInfoRepo(run_info_by_session)
    graph = _FakeGraph(graph_nodes)
    svc = _CallbackTestService(
        graph=graph, engine=engine, callback_repo=callback_repo, run_info_repo=run_info_repo,
    )
    return svc, engine, callback_repo, run_info_repo


def _run_info(task_id: str, node_id: str, session_id: str) -> TaskNodeRunInfoRecord:
    return TaskNodeRunInfoRecord(
        id=0, node_id=node_id, task_id=task_id, run_mode="coop_group", assignee="g1",
        output=None, acceptance_result=None, retry=0, session_id=session_id,
        extend_props=None, start_time=1, update_time=1, end_time=None,
    )


def _req(body: dict | str | bytes) -> _FakeRequest:
    if isinstance(body, (dict, list)):
        raw = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    else:
        raw = body
    return _FakeRequest(raw)


def _default_enricher():
    """真 ``CallbackDataEnricher``(LocalBcsTokenProvider);``enrich_bcn`` 短连 httpx 由 ``_install_fake_bcs`` 打桩。"""
    return CallbackDataEnricher(LocalBcsTokenProvider(base_url="http://bcs"))


def _dispatch_call(req: _FakeRequest, svc, auth, registry, enricher=None):
    """直接调用被 ``@envelope_errors`` 包裹的端点函数(成功 → ``Envelope``,异常 → 上抛)。"""
    return router.report_callback(
        request=req, svc=svc, auth=auth, registry=registry,
        enricher=enricher or _default_enricher(),
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ok_envelope(result):
    """断言成功路径返回 ``Envelope{code=OK,data={"ok":True}}``。"""
    from agentclaw.community.adapters.http.openapi_v1.responses import CODE_OK
    assert result.code == CODE_OK, f"expected OK envelope, got {result!r}"
    assert result.data == {"ok": True}, f"expected ok envelope data, got {result!r}"
    return result


# ===== ClawMind =====

class TestClawMind:
    """ClawMind ``HttpCallbackPayload`` → ``ingest`` 仅落审计,不推进编排核、不收敛。"""

    def test_claw_mind_persists_full_fields_and_advances_nothing(self):
        svc, engine, repo, _ri = _make_svc()
        result = _run(_dispatch_call(_req(_CLAW_MIND_BODY), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)

        # 仅落 ``task_callback``,不推进编排核(/callback/report 固定 result,且 ClawMind 走 ingest)
        assert engine.reports == [] and engine.starts == []
        assert len(repo.calls) == 1
        rec = repo.calls[0]
        # 转换后落库字段(对齐 task_callback 列 + 语雀 §八)
        assert rec.invoker == "claw_mind"
        assert rec.run_id == "flow-abc-123"  # loop_task_id = flow_id(run 实例,对齐 BCN)
        assert rec.node_id == ""                      # workflow 级回投,node_id 空
        assert rec.main_session_id == "S-9"           # origin_session_id → main_session_id
        assert rec.status == "DONE"                   # flow_runs.status=succeeded → task Status.DONE(非顶层 node_succeeded)
        assert rec.result_success is True
        assert rec.result == {"success": True, "data": {"answer": 42}}
        assert rec.exec_error is None
        # execution_graph 已转结构化 TaskExecutionGraph(graph_to_dict 形状),非原始 ext_info 透传
        eg = rec.execution_graph
        assert eg["run_id"] == 0 and eg["status"] == "DONE"
        assert eg["extend_props"] == {"flow_id": "flow-abc-123", "origin_session_id": "S-9"}
        assert len(eg["tasks"]) == 1
        assert eg["tasks"][0]["node_id"] == "N1"
        assert eg["tasks"][0]["status"] == "DONE"
        assert eg["tasks"][0]["task_spec"]["metadata"]["title"] == "N1"   # 无 node_title → 退 node_id
        assert eg["tasks"][0]["run_info"]["output"] == {"answer": 42}
        assert eg["relations"] == []
        assert rec.extend_props is None               # claw_mind 无额外扩展
        assert json.loads(rec.orig_callback_data) == _CLAW_MIND_BODY  # 原始 body

    def test_claw_mind_failed_node_maps_exec_error_and_success_false(self):
        body = {
            "workflow_id": "w", "flow_id": "f", "status": "node_failed",
            "ext_info": {
                "flow_runs": {"origin_session_id": "S-1"},
                "node_executions": [{"node_id": "N1", "status": "failed",
                                      "output_json": None, "error_text": "boom"}],
            },
        }
        svc, engine, repo, _ri = _make_svc()
        _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        rec = repo.calls[0]
        assert rec.run_id == "f"  # loop_task_id = flow_id(run 实例,对齐 BCN)
        assert rec.status == "FAILED"              # node status=failed → task Status.FAILED
        assert rec.main_session_id == "S-1"
        assert rec.result_success is False
        assert rec.exec_error == "boom"
        assert engine.reports == []  # 仍不推进编排核(ClawMind 仅审计)

    def test_claw_mind_does_not_converge_even_on_workflow_success(self):
        # 文档语义:ClawMind 回投只落审计,框架节点状态不由此路径翻态。
        svc, engine, _repo, _ri = _make_svc(
            run_info_by_session={"S-9": _run_info("task-99", "root", "S-9")},
        )
        _run(_dispatch_call(_req(_CLAW_MIND_BODY), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        assert engine.reports == []  # 即便 session_id 能反查到节点,ClawMind 也不收敛


# ===== BCN manager-worker(任务协作群)=====

class TestBCNManagerWorker:
    """BCN manager-worker 事件当前由 callback/report 仅确认接收，不落库、不驱动收敛。"""

    @staticmethod
    def _mw_event(event_type: str, *, session_id="s-1", group_id="g1", task_id="task-1",
                  data=None) -> dict:
        scope = {"group_id": group_id, "session_id": session_id}
        if task_id and event_type.startswith("task."):
            scope["task_id"] = task_id
        return _bcn_event(event_type, scope=scope, data=data or {})

    def test_task_assigned_is_acknowledged_without_persistence(self):
        data = {"task_id": "task-1", "manager_id": "bot-m", "worker_id": "bot-w",
                "session_id": "s-1", "assignment": {"included": False, "size_bytes": 42}}
        ev = self._mw_event("task.assigned", data=data)
        svc, engine, repo, _ri = _make_svc()
        _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        assert engine.reports == [] and engine.starts == []
        assert repo.calls == []
        return

    def test_session_completed_is_acknowledged_without_convergence_when_completed(self):
        data = {"completed_by": "bcs-system", "reason": "completed", "summary": "all done"}
        ev = self._mw_event("session.completed", data=data)
        svc, engine, _repo, _ri = _make_svc(
            run_info_by_session={"s-1": _run_info("task-99", "root", "s-1")},
        )
        _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        # 当前 manager-worker 分支仅 ack，不反查 session，也不驱动 on_report。
        assert engine.reports == []
        return

    def test_session_completed_is_acknowledged_without_convergence_when_failed(self):
        data = {"completed_by": "bcs-system", "reason": "failed", "summary": "boom"}
        ev = self._mw_event("session.completed", data=data)
        svc, engine, _repo, _ri = _make_svc(
            run_info_by_session={"s-1": _run_info("task-99", "root", "s-1")},
        )
        _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        # 当前 manager-worker 分支仅 ack，不执行失败态收敛。
        assert engine.reports == []
        return

    def test_manager_worker_events_are_acknowledged_without_persistence(self):
        sid = "s-1"
        events = [
            self._mw_event("group.created", data={"status": "active", "name": "G",
                                                  "group_kind": "normal"}),
            self._mw_event("session.created", data={"status": "active", "session_kind": "task"}),
            self._mw_event("task.assigned", task_id="t-a",
                           data={"task_id": "t-a", "manager_id": "m", "worker_id": "w",
                                 "session_id": sid, "assignment": {"size_bytes": 1}}),
            self._mw_event("task.completed", task_id="t-a",
                           data={"task_id": "t-a", "manager_id": "m", "worker_id": "w",
                                 "session_id": sid, "result": {"size_bytes": 9},
                                 "completed_at": "T"}),
        ]
        svc, engine, repo, _ri = _make_svc()
        for ev in events:
            _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                                InMemoryCallbackCorrelationRegistry()))
        # manager-worker 事件当前仅 ack，不进入 apply_manager_worker_event。
        assert repo.calls == []
        assert engine.reports == []


# ===== BCN state-machine(自定义协作群)=====

class TestBCNStateMachine:
    """BCN state-machine 事件 → ``translate_bcn`` + BCS run 明细/DAG → ``ingest`` + 终态收敛。"""

    @staticmethod
    def _sm_event(event_type: str, *, run_id="run-1", session_id="s-1", node_id=None,
                  data=None) -> dict:
        scope = {"group_id": "g1", "session_id": session_id, "run_id": run_id}
        d = data or {}
        if node_id:
            d = {"node_id": node_id, **d}
        return _bcn_event(event_type, scope=scope, data=d)

    def test_node_completed_with_run_still_running_persists_but_no_converge(self, monkeypatch):
        run_detail = {"run": {"status": "running", "output": {}},
                      "nodes": [{"node_id": "N1", "status": "completed", "attempt": 1,
                                 "outcome": "success", "artifact_text": "x"}]}
        graph_detail = {"definition": {"name": "sm"},
                        "nodes": [{"node_id": "N1", "display_name": "Step1", "kind": "task",
                                   "assignee": "b1", "final_output": "x"}],
                        "edges": [{"src": "N1", "dst": "N2"}]}
        _install_fake_bcs(monkeypatch, run_detail=run_detail, graph_detail=graph_detail)
        ev = self._sm_event("state_machine.node.completed", node_id="N1",
                            data={"attempt": 1, "outcome": "success",
                                  "output": {"answer": 7}, "completed_at": "T"})
        svc, engine, repo, _ri = _make_svc()
        result = _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)
        assert engine.reports == []  # run 仍 running,不收敛
        assert len(repo.calls) == 2   # 两步落库:原始 + enrich 后更新
        rec = repo.get("run-1", "")
        assert rec.invoker == "bcn"
        assert rec.run_id == "run-1" and rec.node_id == ""  # req4:node_id 恒空(忽略事件 data.node_id)
        assert rec.main_session_id == "s-1"
        assert rec.status == "RUNNING"                     # req2:node.completed → RUNNING
        assert rec.result_success is True
        # run 明细经 result._ext_info 落库 → extend_props;result 同体携带 _ext_info(既有映射)
        assert rec.result == {"success": True, "data": {"answer": 7}, "_ext_info": run_detail}
        # req3:取回的原始 run 明细 → extend_props;orig_callback_data 保持原始 CloudEvent(不再覆盖)
        assert rec.extend_props == run_detail
        assert json.loads(rec.orig_callback_data) == ev
        # req1:execution_graph = graph_to_dict 形状 TaskExecutionGraph(run 详情 + DAG 合并)
        eg = rec.execution_graph
        assert eg["run_id"] == 0 and eg["task_id"] == "" and eg["loop_round"] == 0
        assert eg["status"] == "RUNNING"                   # event_type=node.completed → 图级 RUNNING
        assert eg["output"] == {}                          # run_detail.run.output
        assert eg["extend_props"]["run_status"] == "running"
        assert eg["extend_props"]["definition"] == {"name": "sm"}
        assert len(eg["tasks"]) == 1 and eg["tasks"][0]["node_id"] == "N1"
        assert eg["tasks"][0]["status"] == "DONE"           # 节点执行 status=completed → DONE
        assert eg["tasks"][0]["task_spec"]["metadata"]["title"] == "Step1"  # display_name
        assert eg["tasks"][0]["run_info"]["assignee"] == "b1"
        assert eg["tasks"][0]["run_info"]["extend_props"] == {"attempt": 1, "outcome": "success",
                                                              "status": "completed"}
        assert eg["relations"] == [{"src_id": "N1", "dst_id": "N2",
                                    "type": "DEPENDENCY", "extend_props": {}}]

    def test_run_completed_converges_to_done(self, monkeypatch):
        run_detail = {"run": {"status": "completed", "output": {"final": "ok"}}, "nodes": []}
        _install_fake_bcs(monkeypatch, run_detail=run_detail)
        ev = self._sm_event("state_machine.run.completed",
                            data={"completed_at": "T", "output": {"final": "ok"}, "duration_ms": 9})
        svc, engine, repo, _ri = _make_svc(
            run_info_by_session={"s-1": _run_info("task-99", "root", "s-1")},
        )
        result = _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)
        # ingest 落审计(run-1,"") + converge 落收敛行(task-99,"root")
        assert {r.run_id for r in repo.calls} == {"run-1", "task-99"}
        assert len(engine.reports) == 1
        patch = engine.reports[0]
        assert (patch.task_id, patch.node_id) == ("task-99", "root")
        assert patch.acceptance_result.verdict == AcceptanceVerdict.DONE  # → DONE
        assert patch.output_patch == {"output": {"final": "ok"}}

    def test_run_failed_converges_to_failed(self, monkeypatch):
        run_detail = {"run": {"status": "failed", "output": {"err": "x"}}, "nodes": []}
        _install_fake_bcs(monkeypatch, run_detail=run_detail)
        ev = self._sm_event("state_machine.run.completed",
                            data={"completed_at": "T", "output": {"err": "x"}})
        svc, engine, _repo, _ri = _make_svc(
            run_info_by_session={"s-1": _run_info("task-99", "root", "s-1")},
        )
        _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        # failed run → acceptance FAIL → 节点 FAILED(converge_by_session 给失败分支补 gaps)。
        assert len(engine.reports) == 1
        patch = engine.reports[0]
        assert patch.acceptance_result is not None
        assert patch.acceptance_result.verdict == AcceptanceVerdict.FAILED

    def test_run_completed_converges_even_when_bcs_fetch_fails(self, monkeypatch):
        # BCS run 明细 fetch 失败时,事件本身 state_machine.run.completed 已表明 run 成功完成
        # → 用事件体兜底收敛,不依赖 fetch(success 由事件推,output 取事件 data.output)。
        _install_fake_bcs(monkeypatch, raise_on_get=True)
        ev = self._sm_event("state_machine.run.completed",
                            data={"completed_at": "T", "output": {"final": "ok"}})
        svc, engine, _repo, _ri = _make_svc(
            run_info_by_session={"s-1": _run_info("task-99", "root", "s-1")},
        )
        result = _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)
        patch = engine.reports[0]
        assert (patch.task_id, patch.node_id) == ("task-99", "root")
        assert patch.acceptance_result.verdict == AcceptanceVerdict.DONE  # → DONE
        assert patch.output_patch == {"output": {"final": "ok"}}

    def test_fetch_failure_falls_back_to_raw_cloudevent_but_still_converges(self, monkeypatch):
        """fetch 失败时:审计行仍 fallback 落原始 CloudEvent(_raw_callback_body 未覆盖),
        但终态收敛改由事件本身兜底(不再被 fetch 失败吞掉)。"""
        _install_fake_bcs(monkeypatch, raise_on_get=True)
        ev = self._sm_event("state_machine.run.completed",
                            data={"completed_at": "T", "output": {"final": "ok"}})
        svc, engine, repo, _ri = _make_svc(
            run_info_by_session={"s-1": _run_info("task-99", "root", "s-1")},
        )
        result = _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)
        # 收敛已触发(事件兜底)
        assert len(engine.reports) == 1
        assert engine.reports[0].acceptance_result.verdict == AcceptanceVerdict.DONE
        # ingest 审计行(run-1,"")仍 fallback 落原始 CloudEvent;converge 另落一行(task-99,"root")
        ingest = repo.get("run-1", "")
        assert ingest is not None
        assert json.loads(ingest.orig_callback_data) == ev  # 原始 CloudEvent(未覆盖)
        assert ingest.extend_props is None                 # fetch 失败 → 无 run 明细可落 extend_props
        # req1:fetch 失败兜底 → execution_graph 为极简 graph_to_dict(非事件体透传)
        eg = ingest.execution_graph
        assert eg["run_id"] == 0 and eg["status"] == "DONE"  # run.completed → DONE
        assert eg["output"] == {"final": "ok"}               # 兜底取事件 data.output
        assert eg["tasks"] == [] and eg["relations"] == [] and eg["extend_props"] == {}

    def test_bcs_non_200_fetch_falls_back_to_raw_event(self, monkeypatch):
        _install_fake_bcs(monkeypatch, run_status=500)
        ev = self._sm_event("state_machine.run.completed",
                            data={"completed_at": "T", "output": {}})
        svc, engine, repo, _ri = _make_svc()
        _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        assert engine.reports == []
        rec = repo.get("run-1", "")
        assert json.loads(rec.orig_callback_data) == ev  # 非 200 → 用原始事件
        assert rec.extend_props is None                   # run 明细非 200 → 无 extend_props
        eg = rec.execution_graph                          # fetch 兜底 → 极简 graph_to_dict
        assert eg["status"] == "DONE" and eg["output"] == {}

    def test_handled_event_without_node_uses_run_id_as_loop_task_id(self, monkeypatch):
        _install_fake_bcs(monkeypatch, run_detail={"run": {"status": "running"}, "nodes": []})
        ev = self._sm_event("state_machine.run.started",
                            data={"run_mode": "configured", "started_at": "T"})
        svc, _engine, repo, _ri = _make_svc()
        _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        rec = repo.get("run-1", "")
        assert rec.run_id == "run-1" and rec.node_id == ""  # 无 node_id(req4 恒空)
        assert rec.status == "RUNNING"                       # req2:run.started → RUNNING
        assert rec.extend_props == {"run": {"status": "running"}, "nodes": []}  # req3 run 明细

    def test_non_handled_bcn_event_acks_without_persist(self):
        # message.created 非 manager-worker、非 handled state_machine → 不落库、不收敛、ack 带说明
        ev = _bcn_event("message.created",
                        scope={"group_id": "g1", "session_id": "s-1"},
                        data={"logical_message_id": "m1", "message_type": "chat"})
        svc, engine, repo, _ri = _make_svc()
        result = _run(_dispatch_call(_req(ev), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        assert result.data == {"ok": True}
        assert result.message == "bcn event not handled"
        assert repo.calls == [] and engine.reports == []


# ===== manager-worker merge 纯函数(当前零覆盖)=====

def _parsed(event_type: str, *, task_id="t-a", session_id="s-1", group_id="g1",
            data=None) -> dict:
    return {"event_id": "evt-1", "event_type": event_type, "group_id": group_id,
            "session_id": session_id, "task_id": task_id, "data": data or {}}


class TestManagerWorkerMerge:
    """``merge_manager_worker_execution_graph`` / ``parse_manager_worker_bcn`` 纯函数覆盖。"""

    def test_parse_returns_none_for_non_manager_worker_events(self):
        for et in ("state_machine.run.completed", "state_machine.node.started",
                   "message.created", "group.foo"):
            assert parse_manager_worker_bcn(_bcn_event(et, scope={"group_id": "g"})) is None
        assert parse_manager_worker_bcn(None) is None

    def test_parse_extracts_scope_and_data(self):
        ev = _bcn_event("task.assigned",
                        scope={"group_id": "g1", "session_id": "s-1", "task_id": "t-a"},
                        data={"task_id": "t-a", "manager_id": "m", "worker_id": "w"})
        parsed = parse_manager_worker_bcn(ev)
        assert parsed == {"event_id": "evt-1", "event_type": "task.assigned",
                          "group_id": "g1", "session_id": "s-1", "task_id": "t-a",
                          "data": {"task_id": "t-a", "manager_id": "m", "worker_id": "w"}}

    def test_group_created_sets_group_status(self):
        merged = merge_manager_worker_execution_graph(None, _parsed(
            "group.created", data={"status": "active", "name": "G"}))
        assert merged["group_status"] == "active"
        assert merged["last_event_type"] == "group.created"
        assert merged["tasks"] == []

    def test_session_created_defaults_active_status(self):
        merged = merge_manager_worker_execution_graph(None, _parsed("session.created", data={}))
        assert merged["session_status"] == "active"  # data 无 status → 默认 active

    def test_task_assigned_then_completed_accumulates_in_order(self):
        state = merge_manager_worker_execution_graph(None, _parsed(
            "task.assigned", task_id="t-a", data={"manager_id": "m", "worker_id": "w",
                                                  "assignment": {"size_bytes": 1}}))
        state = merge_manager_worker_execution_graph(state, _parsed(
            "task.completed", task_id="t-a", data={"result": {"size_bytes": 9},
                                                   "completed_at": "T"}))
        assert len(state["tasks"]) == 1
        task = state["tasks"][0]
        assert task["status"] == "completed"            # assigned → completed
        assert task["assignment"] == {"size_bytes": 1}  # assigned 阶段字段保留
        assert task["result"] == {"size_bytes": 9} and task["completed_at"] == "T"

    def test_none_values_do_not_overwrite_existing(self):
        # task.completed 缺 manager_id(None)→ 不覆盖 task.assigned 已写的 manager_id
        state = merge_manager_worker_execution_graph(None, _parsed(
            "task.assigned", task_id="t-a", data={"manager_id": "m", "worker_id": "w"}))
        state = merge_manager_worker_execution_graph(state, _parsed(
            "task.completed", task_id="t-a",
            data={"manager_id": None, "worker_id": None, "result": {"ok": 1}}))
        assert state["tasks"][0]["manager_id"] == "m"   # None 不覆盖
        assert state["tasks"][0]["worker_id"] == "w"
        assert state["tasks"][0]["result"] == {"ok": 1}

    def test_session_completed_records_reason_summary_completed_by(self):
        state = merge_manager_worker_execution_graph(None, _parsed(
            "session.completed", data={"reason": "failed", "completed_by": "bcs-system",
                                       "summary": "boom"}))
        assert state["session_status"] == "failed"
        assert state["session_completed_by"] == "bcs-system"
        assert state["session_summary"] == "boom"

    def test_task_completed_survives_late_assigned(self):
        """乱序兜底:同 task 的 completed 早于 assigned 到达时,后到的 assigned 不得把
        已完成的 status 回退为 assigned(_upsert_task_entry 对 status 单调保护)。语雀
        保证同 task 严格按 stream.sequence 投递,正常链路不触发;此为乱序容忍的落地。"""
        state = merge_manager_worker_execution_graph(None, _parsed(
            "task.completed", task_id="t-a", data={"result": {"ok": 1}, "completed_at": "T"}))
        assert state["tasks"][0]["status"] == "completed"
        state = merge_manager_worker_execution_graph(state, _parsed(
            "task.assigned", task_id="t-a",
            data={"manager_id": "m", "worker_id": "w", "assignment": {"size_bytes": 1}}))
        # status 不回退,仍为 completed;assigned 阶段元数据(manager/worker/assignment)照常补齐
        task = state["tasks"][0]
        assert task["status"] == "completed"
        assert task["manager_id"] == "m" and task["worker_id"] == "w"
        assert task["assignment"] == {"size_bytes": 1}

    def test_different_tasks_do_not_clobber_each_other(self):
        state = merge_manager_worker_execution_graph(None, _parsed(
            "task.assigned", task_id="t-a", data={"manager_id": "m", "worker_id": "w"}))
        state = merge_manager_worker_execution_graph(state, _parsed(
            "task.assigned", task_id="t-b", data={"manager_id": "m2", "worker_id": "w2"}))
        assert {t["task_id"] for t in state["tasks"]} == {"t-a", "t-b"}


# ===== 羽雀/框架节点级回投(TaskCallbackRequest)=====

class TestFrameworkCallback:
    """common_task 回投(body 顶层 task_id+node_id+status+output)→ translate_common_task_callback
    → report_result → _adapt_common_task → on_report 推进编排核。

    latest 路由弃用 loop_task_id 回声/registry 解析:node_id 直取 body 顶层;verdict/gaps 经
    body.acceptance_result dict(非 is_success/failed_info);invoker 落 translate 固定 workflow_source="task_loop"。"""

    @staticmethod
    def _req_body(**kw) -> dict:
        d = dict(task_id="t1", node_id="c1", status="DONE", output={"r": 1},
                 acceptance_result={"verdict": "DONE", "gaps": []},
                 workflow_instance_id="i1")
        d.update(kw)
        return d

    def test_node_level_report_result_advances_engine_with_pass(self):
        body = self._req_body()
        svc, engine, repo, _ri = _make_svc()
        result = _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)
        assert engine.starts == []                      # /callback/report 固定 result,不 start_run
        assert len(engine.reports) == 1
        patch = engine.reports[0]
        assert (patch.task_id, patch.node_id) == ("t1", "c1")  # body 顶层 node_id 直取
        assert patch.status == Status.DONE
        assert patch.acceptance_result.verdict == AcceptanceVerdict.DONE
        assert patch.output_patch == {"output": {"r": 1}}
        # common_task 路由不经 auth.verify;invoker 落 translate 固定 workflow_source="task_loop"
        assert repo.calls[0].invoker == "task_loop"
        assert repo.calls[0].run_id == "t1" and repo.calls[0].node_id == "c1"

    def test_failed_framework_result_routes_to_fail_acceptance_with_gaps(self):
        body = self._req_body(status="FAILED", output=None,
                              acceptance_result={"verdict": "FAILED", "gaps": ["证据不足"]})
        svc, engine, _repo, _ri = _make_svc()
        _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        patch = engine.reports[0]
        assert patch.acceptance_result.verdict == AcceptanceVerdict.FAILED
        assert patch.acceptance_result.gaps == ["证据不足"]  # acceptance_result.gaps 直传

    def test_task_level_uses_explicit_node_id(self):
        # common_task 路由用 body 顶层 node_id 定位节点(无 loop_task_id 回声/registry 解析)
        body = self._req_body(node_id="root1")
        svc, engine, _repo, _ri = _make_svc()
        _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        assert (engine.reports[0].task_id, engine.reports[0].node_id) == ("t1", "root1")

    def test_task_level_routes_without_acceptance_dict(self):
        # 无 acceptance_result dict 时仍按 common_task 路由(task_id+node_id+status+output 满足即可)
        body = {"task_id": "t1", "node_id": "root1", "status": "DONE", "output": {}}
        svc, engine, _repo, _ri = _make_svc()
        _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                            InMemoryCallbackCorrelationRegistry()))
        assert (engine.reports[0].task_id, engine.reports[0].node_id) == ("t1", "root1")
        assert engine.reports[0].acceptance_result is None


# ===== common_task 推进 / 非 JSON 422 / 非法 JSON body =====

class TestFallbackAndInvalid:
    def test_common_task_report_result_advances_engine(self):
        # common_task 路由(task_id+node_id+status+output)→ report_result 推进编排核
        body = {"task_id": "t1", "node_id": "c1", "status": "DONE", "output": "done",
                "acceptance_result": {"verdict": "DONE", "gaps": []}}
        svc, engine, repo, _ri = _make_svc()
        result = _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)
        assert len(engine.reports) == 1
        patch = engine.reports[0]
        assert (patch.task_id, patch.node_id) == ("t1", "c1")
        assert patch.acceptance_result.verdict == AcceptanceVerdict.DONE
        # translate_common_task_callback 固定 workflow_source="task_loop" → invoker;无 instance → main_session_id 空
        assert repo.calls[0].invoker == "task_loop"
        assert repo.calls[0].main_session_id == ""

    def test_non_json_body_raises_http_422(self):
        from fastapi import HTTPException
        svc, engine, _repo, _ri = _make_svc()
        with pytest.raises(HTTPException) as excinfo:
            _run(_dispatch_call(_req("not-json{"), svc, NoopCallbackAuthenticator(),
                                InMemoryCallbackCorrelationRegistry()))
        assert excinfo.value.status_code == 422
        assert engine.reports == [] and engine.starts == []

    def test_invalid_json_dict_acks_without_advancing(self):
        # 合法 JSON 但不满足 claw_mind/bcn/common_task 任一分流 → 200 ack,不推进编排核(仅非 JSON 才 422)
        svc, engine, _repo, _ri = _make_svc()
        result = _run(_dispatch_call(_req({"foo": "bar"}), svc, NoopCallbackAuthenticator(),
                                     InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(result)
        assert engine.reports == [] and engine.starts == []


# ===== 幂等:result 重投到已终态节点 =====

class TestIdempotency:
    """common_task 回投的幂等/上抛语义:同一 result 重投使用稳定 event_id 并直接 ack;
    非幂等的非法翻态仍上抛 TaskStateError。"""

    @staticmethod
    def _body(node_id="n1", status="DONE"):
        return {"task_id": "t1", "node_id": node_id, "status": status, "output": {"r": 1},
                "acceptance_result": {"verdict": "DONE", "gaps": []}}

    def test_result_replay_is_idempotent_and_returns_200(self):
        # 同一 result 首次处理和重复处理都返回 200,重复请求不再次驱动 on_report。
        engine = _RecordingEngine()
        svc, _e, repo, _ri = _make_svc(
            engine=engine,
            graph_nodes={"t1": [_node("t1", "n1", Status.SUCCESS)]},
        )
        body = self._body()
        r1 = _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                                 InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(r1)
        assert len(engine.reports) == 1
        first_id = repo.calls[0].event_id
        assert first_id is not None

        r2 = _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                                 InMemoryCallbackCorrelationRegistry()))
        _ok_envelope(r2)
        assert len(engine.reports) == 1
        assert len(repo.calls) == 1
        assert repo.calls[0].event_id == first_id

    def test_result_replay_to_non_terminal_re_raises_task_state_error(self):
        # on_report 抛 TaskStateError(非法翻态)→ _dispatch 上抛(envelope_errors→409,单测直接捕异常)
        engine = _TaskStateErrorEngine(terminal=False)
        svc, _e, _repo, _ri = _make_svc(
            engine=engine,
            graph_nodes={"t1": [_node("t1", "n1", Status.RUNNING)]},
        )
        with pytest.raises(TaskStateError):
            _run(_dispatch_call(_req(self._body()), svc, NoopCallbackAuthenticator(),
                                InMemoryCallbackCorrelationRegistry()))


# ===== 分流选择 + 鉴权 source =====

class _RecordingAuth:
    """记录 ``verify`` 调用 source 的鉴权替身(永不失败;失败路径用真实 HmacCallbackAuthenticator)。"""

    def __init__(self) -> None:
        self.sources: list[str] = []

    def verify(self, *, source, headers, raw_body, method, path) -> None:
        self.sources.append(source)


class TestDispatchSelectionAndAuth:
    """按 body 形态分流至不同 svc 入口;auth.verify 的 source 与端点 disposition 校验。"""

    def test_claw_mind_routes_to_ingest_only(self):
        svc, engine, _repo, _ri = _make_svc()
        auth = _RecordingAuth()
        _run(_dispatch_call(_req(_CLAW_MIND_BODY), svc, auth,
                            InMemoryCallbackCorrelationRegistry()))
        assert auth.sources == ["claw_mind"]
        assert engine.reports == [] and engine.starts == []  # ingest 不推进

    def test_bcn_manager_worker_routes_to_apply_manager_worker_event(self):
        ev = _bcn_event("task.assigned",
                        scope={"group_id": "g1", "session_id": "s-1", "task_id": "t-a"},
                        data={"task_id": "t-a", "manager_id": "m", "worker_id": "w"})
        svc, engine, repo, _ri = _make_svc()
        auth = _RecordingAuth()
        _run(_dispatch_call(_req(ev), svc, auth, InMemoryCallbackCorrelationRegistry()))
        assert auth.sources == ["bcn"]
        # manager-worker 分支当前仅 ack，不调用 apply_manager_worker_event。
        assert repo.calls == []
        assert engine.reports == []

    def test_bcn_state_machine_routes_to_ingest_with_bcn_auth(self, monkeypatch):
        _install_fake_bcs(monkeypatch, run_detail={"run": {"status": "running"}, "nodes": []})
        ev = _bcn_event("state_machine.node.completed",
                        scope={"group_id": "g1", "session_id": "s-1", "run_id": "run-1"},
                        data={"node_id": "N1", "attempt": 1, "outcome": "success"})
        svc, engine, repo, _ri = _make_svc()
        auth = _RecordingAuth()
        _run(_dispatch_call(_req(ev), svc, auth, InMemoryCallbackCorrelationRegistry()))
        assert auth.sources == ["bcn"]
        assert repo.calls[0].invoker == "bcn"  # state-machine 走 translate_bcn+ingest

    def test_common_task_route_does_not_invoke_auth(self):
        # common_task 分流不经 auth.verify(仅 claw_mind/bcn 鉴权)。
        body = {"task_id": "t1", "node_id": "c1", "status": "DONE",
                "output": {"r": 1}}
        svc, engine, _repo, _ri = _make_svc()
        auth = _RecordingAuth()
        _run(_dispatch_call(_req(body), svc, auth, InMemoryCallbackCorrelationRegistry()))
        assert auth.sources == []
        assert len(engine.reports) == 1

    def test_callback_report_never_calls_start_run(self, monkeypatch):
        # /callback/report 固定 disposition="result";所有形态均不应触发 start_run。
        _install_fake_bcs(monkeypatch, run_detail={"run": {"status": "running"}, "nodes": []})
        bodies = [
            _CLAW_MIND_BODY,
            _bcn_event("state_machine.node.completed",
                       scope={"group_id": "g1", "session_id": "s-1", "run_id": "run-1"},
                       data={"node_id": "N1", "attempt": 1, "outcome": "success"}),
            {"task_id": "t1", "workflow_source": "bcn", "workflow_id": "w7",
             "workflow_instance_id": "i1", "status": "COMPLETED", "is_success": True,
             "loop_task_id": "t1::c1"},
        ]
        for body in bodies:
            svc, engine, _repo, _ri = _make_svc()
            _run(_dispatch_call(_req(body), svc, NoopCallbackAuthenticator(),
                                InMemoryCallbackCorrelationRegistry()))
            assert engine.starts == [], f"start_run 不应在 /callback/report 被调用: {body}"

    def test_auth_failure_raises_callback_auth_error(self):
        # 真实 HmacCallbackAuthenticator:伪造回调(无签名头)→ raise CallbackAuthError(→ 401),
        # 而非 ValidationError(→ 400)。对齐 auth.py docstring 与 ENVELOPE_ERRORS。
        from agentclaw.community.adapters.http.task.auth import HmacCallbackAuthenticator
        from agentclaw.community.core.errors import CallbackAuthError
        svc, _engine, _repo, _ri = _make_svc()
        auth = HmacCallbackAuthenticator(secrets={"claw_mind": "k"})  # headers 空缺 → 校验失败
        with pytest.raises(CallbackAuthError):
            _run(_dispatch_call(_req(_CLAW_MIND_BODY), svc, auth,
                                InMemoryCallbackCorrelationRegistry()))
