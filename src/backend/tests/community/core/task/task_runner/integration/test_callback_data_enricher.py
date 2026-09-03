"""``CallbackDataEnricher`` 单测 —— 统一处理 BCN(查 BCS run 明细)+ ClawMind(从 ext_info 构图)两路回投数据。

- ``enrich_claw_mind``:sync 纯构建(execution_graph 从 ext_info),无 IO。
- ``enrich_bcn``:经注入 httpx(MockTransport)查 BCS GET run 明细 + DAG,
  落 ``result._ext_info``(→ extend_props)并构建 图;fetch 失败/非 200 不抛,用事件体兜底建极简图,返 run_detail 供收敛。
"""
from __future__ import annotations

import asyncio

import httpx

from agentclaw.community.core.task.domain.models import TaskCallbackData
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)


def _provider(base_url: str = "http://bcs") -> LocalBcsTokenProvider:
    return LocalBcsTokenProvider(base_url=base_url)


def _bcd(data: dict | None = None) -> TaskCallbackData:
    return TaskCallbackData(data=dict(data or {}))


def _sm_event(event_type: str, run_id: str = "run-1", data: dict | None = None) -> dict:
    return {
        "event_id": "evt-1", "event_type": event_type, "source": "bcs",
        "scope": {"group_id": "g1", "session_id": "s-1", "run_id": run_id},
        "data": data or {},
    }


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _enricher(handler, *, base_url: str = "http://bcs") -> CallbackDataEnricher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)
    return CallbackDataEnricher(_provider(base_url), http_client=client)


class TestEnrichClawMind:
    """ClawMind:ext_info → graph_to_dict 形状 execution_graph(纯构建,无 IO)。"""

    def test_builds_graph_to_dict_from_ext_info(self):
        raw = {"ext_info": {
            "flow_runs": {"id": "fr1", "status": "succeeded", "origin_session_id": "S-9"},
            "node_executions": [{"node_id": "N1", "status": "succeeded",
                                  "output_json": '{"answer": 42}'}]}}
        cd = _bcd({"loop_task_id": "flow-1", "status": "DONE", "result": {}})
        CallbackDataEnricher(_provider()).enrich_claw_mind(cd, raw)
        eg = cd.data["execution_graph"]
        assert eg["run_id"] == 0                       # flow_runs.id="fr1" 非整 → 0
        assert eg["status"] == "DONE"                   # succeeded → DONE
        assert eg["tasks"][0]["node_id"] == "N1"
        assert eg["tasks"][0]["status"] == "DONE"
        assert eg["tasks"][0]["task_spec"]["metadata"]["title"] == "N1"   # 无 node_title → 退 node_id
        assert eg["tasks"][0]["run_info"]["output"] == {"answer": 42}
        assert eg["extend_props"]["origin_session_id"] == "S-9"
        assert eg["relations"] == []                    # N1 无 nodeOutputKeys

    def test_empty_ext_leaves_no_execution_graph(self):
        cd = _bcd({"status": "RUNNING"})
        CallbackDataEnricher(_provider()).enrich_claw_mind(cd, {"ext_info": {}})
        assert "execution_graph" not in cd.data         # _build_claw_mind 返 None → 不设


class TestEnrichBcn:
    """BCN:查 BCS run 明细 + DAG → enrich;fetch 失败/非 200 兜底极简图,不抛。"""

    def test_fetch_and_enrich_full_graph(self):
        rd = {"run": {"status": "running", "output": {}},
              "nodes": [{"node_id": "N1", "status": "completed", "attempt": 1,
                         "outcome": "success", "artifact_text": "x"}]}
        gd = {"definition": {"name": "sm"},
              "nodes": [{"node_id": "N1", "display_name": "Step1", "kind": "task",
                         "assignee": "b1", "final_output": "x"}],
              "edges": [{"src": "N1", "dst": "N2"}]}

        def handler(req):
            if req.url.path.endswith("/graph"):
                return httpx.Response(200, json=gd)
            return httpx.Response(200, json=rd)

        raw = _sm_event("state_machine.node.completed", data={"output": {"answer": 7}})
        cd = _bcd({"loop_task_id": "run-1", "status": "RUNNING", "result": {}})
        run_detail = _run(_enricher(handler).enrich_bcn(cd, raw, "run-1"))

        assert run_detail == rd                          # 返回 run_detail 供收敛
        assert cd.data["result"]["_ext_info"] == rd      # → extend_props
        eg = cd.data["execution_graph"]
        assert eg["run_id"] == 0 and eg["status"] == "RUNNING"   # node.completed → 图 RUNNING
        assert eg["output"] == {}                        # run_detail.run.output
        assert eg["extend_props"]["run_status"] == "running"
        assert eg["extend_props"]["definition"] == {"name": "sm"}
        assert eg["tasks"][0]["node_id"] == "N1"
        assert eg["tasks"][0]["status"] == "DONE"        # 节点 completed → DONE
        assert eg["tasks"][0]["task_spec"]["metadata"]["title"] == "Step1"   # display_name
        assert eg["relations"] == [{"src_id": "N1", "dst_id": "N2",
                                    "type": "DEPENDENCY", "extend_props": {}}]

    def test_fetch_GET_paths_use_provider_base_url(self):
        """enrich_bcn 走 {base}/state-machine-runs/{run_id} 与 /graph;base_url 取自 provider。"""
        seen = []

        def handler(req):
            seen.append(req.url.path)
            return httpx.Response(200, json={"run": {"status": "running"}, "nodes": []})

        _run(_enricher(handler, base_url="http://bcs").enrich_bcn(
            _bcd({"result": {}}), _sm_event("state_machine.run.started"), "run-7"))
        assert seen == ["/state-machine-runs/run-7", "/state-machine-runs/run-7/graph"]

    def test_fetch_exception_fallback_minimal_no_raise(self):
        def handler(req):
            raise RuntimeError("bcs unreachable")

        raw = _sm_event("state_machine.run.completed", data={"output": {"final": "ok"}})
        cd = _bcd({"loop_task_id": "run-1", "status": "DONE", "result": {}})
        run_detail = _run(_enricher(handler).enrich_bcn(cd, raw, "run-1"))

        assert run_detail is None                         # fetch 失败
        assert "_ext_info" not in cd.data.get("result", {})   # 无 run 明细 → 不落 extend_props
        eg = cd.data["execution_graph"]                   # 极简兜底图
        assert eg["status"] == "DONE"                     # run.completed → DONE
        assert eg["output"] == {"final": "ok"}            # 兜底取事件 data.output
        assert eg["tasks"] == [] and eg["relations"] == [] and eg["extend_props"] == {}

    def test_non_200_fallback_minimal(self):
        def handler(req):
            return httpx.Response(500, json={})

        raw = _sm_event("state_machine.run.completed", data={"output": {}})
        cd = _bcd({"loop_task_id": "run-1", "status": "DONE", "result": {}})
        run_detail = _run(_enricher(handler).enrich_bcn(cd, raw, "run-1"))

        assert run_detail is None                         # 非 200 → None
        eg = cd.data["execution_graph"]
        assert eg["status"] == "DONE"
        assert eg["output"] == {}


def test_parse_dict_strict_returns_empty_for_unsupported_value():
    from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
        _parse_dict_strict,
    )

    assert _parse_dict_strict(123, field="result_json") == {}
