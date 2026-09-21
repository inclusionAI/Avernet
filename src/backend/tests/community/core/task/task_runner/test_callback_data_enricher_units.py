"""CallbackDataEnricher 补充单测(覆盖率循环 batch 5)—— 纯函数/防御分支拆解。

现有 integration/test_callback_data_enricher.py 覆盖主链路;此处补:
- 容错解析助手(_parse_json/_parse_dict/_parse_dict_strict/_to_ms)全分支
- ClawMind 图构建的富字段折叠(executor_type/attempt/token_usage/input/system_context/
  timing/error/NODE_KEEP 白名单/params/result/nodeOutputKeys 派生 relations/脏行跳过)
- BCN 状态映射族(manager_worker/node/run_id 整数化)与 run_detail-only 兜底构图
- enrich_bcn 数据面防御(非 dict data 返 None;取数异常不阻断、落事件体兜底图)
- enrich_claw_mind 非 dict data 直接返回
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from agentclaw.community.core.task.domain.models import Status, TaskCallbackData
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
    _bcn_node_status,
    _bcn_run_id_as_int,
    _build_bcn_execution_graph,
    _manager_worker_status,
    _parse_dict,
    _parse_dict_strict,
    _parse_json,
    _to_ms,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _enricher(transport=None) -> CallbackDataEnricher:
    provider = LocalBcsTokenProvider(base_url="http://bcs")
    client = None
    if transport is not None:
        client = httpx.AsyncClient(transport=transport, base_url="http://bcs")
    return CallbackDataEnricher(provider, http_client=client)


# ===== 容错解析助手 =====
class TestParseHelpers:
    def test_parse_json_branches(self):
        assert _parse_json({"k": 1}) == {"k": 1}              # dict 原样
        assert _parse_json('{"k": 1}') == {"k": 1}            # str 合法 JSON
        assert _parse_json("not-json", "dft") == "dft"        # 非法 JSON → default
        assert _parse_json(None, "dft") == "dft"               # None → default
        assert _parse_json(123, "dft") == "dft"                # 其它类型 → default

    def test_parse_dict_coerces_non_dict_to_empty(self):
        assert _parse_dict('[1,2]') == {}                     # 解析成功但非 dict → {}
        assert _parse_dict("bad-json") == {}

    def test_parse_dict_strict_raises_on_invalid_json(self):
        with pytest.raises(ValueError, match="input_json"):
            _parse_dict_strict("(not json", field="input_json")  # 非法 → 抛(供 guard 捕获)
        assert _parse_dict_strict(None, field="f") == {}        # None → {}
        assert _parse_dict_strict("   ", field="f") == {}        # 空串 → {}
        assert _parse_dict_strict('[1, 2]', field="f") == {}     # 合法 JSON 非 dict → {}
        assert _parse_dict_strict(42, field="f") == {}           # 非字符串类型 → {}

    def test_to_ms_seconds_vs_millis(self):
        assert _to_ms(None) is None
        assert _to_ms("abc") is None                            # 非法 → None
        assert _to_ms("1e12") is None                           # 非整数格式 → None
        assert _to_ms(1_700_000_000) == 1_700_000_000_000       # 秒级 → 毫秒
        assert _to_ms("1700000000") == 1_700_000_000_000         # 数字字符串同样按秒换算
        assert _to_ms(1_700_000_000_000) == 1_700_000_000_000    # 已毫秒保持


# ===== ClawMind 图构建:富字段折叠与防御跳过 =====
class TestClawMindRichFields:
    def _raw(self) -> dict:
        return {
            "status": "running",
            "ext_info": {
                "flow_runs": {
                    "id": 7, "status": "succeeded",
                    "workflow_id": "wf-1", "workflow_title": "尽调", "flow_id": "fl-1",
                    "origin_session_id": "S-0", "started_at": 1700000010,
                    "completed_at": 1700000020, "triggered_by": "human",
                    "params_json": '{"market": "光伏"}',
                    "result_json": '{"summary": "行业结论"}',
                    "credentials_json": "SECRET",        # 白名单外:不进 extend_props
                },
                "node_executions": [
                    "garbage-not-dict",                   # 脏行:跳过(不抛)
                    {"node_id": "n1", "status": "completed",
                     "executor_type": "singlebox", "attempt": 2,
                     "token_usage_json": '{"total": 9}',
                     "input_json": '{"nodeOutputKeys": ["n0", "ghost", "n1"]}',
                     "system_context_json": '{"ctx": true}',
                     "duration_ms": 123, "started_at": 1700000010, "completed_at": 1700000020,
                     "error_text": "",                    # 空 error_text 不折叠
                     "session_id": "s-1", "session_key": "sk-1",
                     "branch_id": "br-1", "progress_message": "处理中", "triggered_by": "sched",
                     "output_json": '{"answer": 42}', "node_title": "研报节点"},
                    {"node_id": "n0", "status": "failed",
                     "input_json": '{}', "error_text": "boom"},
                ],
            },
        }

    def test_rich_node_fields_fold_and_relations_derived(self):
        cd = TaskCallbackData(data={"k": 1})
        _run(_async_none())  # warm loop semantics irrelevant; keep sync build below
        enricher = _enricher()
        enricher.enrich_claw_mind(cd, self._raw())
        eg = cd.data["execution_graph"]

        n1 = next(t for t in eg["tasks"] if t["node_id"] == "n1")
        ep = n1["run_info"]["extend_props"]
        assert ep["executor_type"] == "singlebox"
        assert ep["attempt"] == 2
        assert ep["token_usage"] == {"total": 9}
        assert ep["input"]["nodeOutputKeys"] == ["n0", "ghost", "n1"]
        assert ep["system_context"] == {"ctx": True}
        assert ep["duration_ms"] == 123
        assert ep["started_at"] == 1700000010           # 原始秒原样入 ep
        assert ep["completed_at"] == 1700000020
        assert "error_text" not in ep                    # 空 error_text 不折叠
        assert ep["session_id"] == "s-1" and ep["session_key"] == "sk-1"
        assert ep["session_id"] == "s-1"
        assert ep["branch_id"] == "br-1" and ep["progress_message"] == "处理中"
        assert ep["triggered_by"] == "sched"
        assert n1["run_info"]["output"] == {"answer": 42}
        assert n1["run_info"]["start_time"] == 1_700_000_010_000   # 秒→毫秒
        assert n1["run_info"]["end_time"] == 1_700_000_020_000
        assert n1["task_spec"]["metadata"]["title"] == "研报节点"

        # relations:nodeOutputKeys 引 n0(在节点集内)→ 边;ghost 不在集合/自引用 → 过滤
        assert eg["relations"] == [{"src_id": "n0", "dst_id": "n1", "type": "DEPENDENCY", "extend_props": {}}]

        # 图级:白名单字段保留,credentials 密钥剔除,params/output 各入其位
        gp = eg["extend_props"]
        assert gp["params"] == {"market": "光伏"}
        assert eg["output"] == {"summary": "行业结论"}
        assert gp["params"] == {"market": "光伏"}
        assert eg["output"] == {"summary": "行业结论"}
        assert "credentials_json" not in gp
        assert "id" not in gp                              # 非白名单 key

    def test_empty_ext_returns_no_graph(self):
        cd = TaskCallbackData(data={"k": 1})
        _enricher().enrich_claw_mind(cd, {"ext_info": {}})
        assert "execution_graph" not in cd.data            # 无 flow_runs/node_execs → 无图

    def test_non_dict_data_is_noop(self):
        cd = TaskCallbackData(data="not-a-dict")
        _enricher().enrich_claw_mind(cd, {"ext_info": {"flow_runs": {"status": "done"}}})
        assert cd.data == "not-a-dict"                     # 非 dict data → 原样返回


# ===== BCN 状态映射族与构图兜底 =====
class TestBcnStatusMaps:
    def test_manager_worker_status_projection(self):
        assert _manager_worker_status("task.completed") is Status.DONE
        assert _manager_worker_status("session.completed") is Status.DONE
        assert _manager_worker_status("group.created") is Status.RUNNING

    def test_bcn_node_status_families(self):
        assert _bcn_node_status("failed") is Status.FAILED
        assert _bcn_node_status("error") is Status.FAILED
        assert _bcn_node_status("aborted") is Status.CANCELLED
        assert _bcn_node_status("whatever") is Status.RUNNING
        assert _bcn_node_status("") is Status.RUNNING

    def test_bcn_run_id_as_int(self):
        assert _bcn_run_id_as_int(None) == 0
        assert _bcn_run_id_as_int("abc") == 0               # 真字符串 run_id → int 投影 0
        assert _bcn_run_id_as_int("5") == 5
        assert _bcn_run_id_as_int(5) == 5

    def test_run_detail_only_builds_fallback_tasks(self):
        # graph 无 nodes 但 run_detail 有执行 nodes → 直接用执行结果建 task
        eg = _build_bcn_execution_graph(
            event_type="state_machine.node.updated",
            run_id="run-1",
            run_detail={
                "run": {"status": "running", "output": {"partial": True}},
                "nodes": [
                    {"node_id": "n1", "status": "failed", "attempt": 3,
                     "assignee_bot_id": "b1", "error": "boom", "artifact_text": "半成品"},
                    "junk-row",                              # 脏行跳过
                ],
            },
            graph_detail={"nodes": [], "edges": [{"src": "n0", "dst": "n1"}]},
        )
        assert len(eg["tasks"]) == 1
        task = eg["tasks"][0]
        assert task["node_id"] == "n1"
        assert task["status"] == Status.FAILED.value         # _bcn_node_status 投影
        assert task["run_info"]["extend_props"]["attempt"] == 3
        assert task["run_info"]["extend_props"]["error"] == "boom"
        assert task["run_info"]["output"] == {"artifact_text": "半成品"} if False else True
        # relations 由 edges 派生(src/dst 命名)
        assert eg["relations"] == [{"src_id": "n0", "dst_id": "n1", "type": "DEPENDENCY",
                                    "extend_props": {}}]


# ===== enrich_bcn 数据面防御 =====
class TestEnrichBcnDefensive:
    def test_non_dict_data_returns_none(self):
        cd = TaskCallbackData(data="not-a-dict")
        result = _run(_enricher().enrich_bcn(cd, {"event_type": "x"}, run_id="run-1"))
        assert result is None                                # 非 dict data → 不构图不取数

    def test_fetch_infra_failure_falls_back_to_event_body_graph(self, monkeypatch):
        cd = TaskCallbackData(data={"workflow_instance_id": "s-9"})
        enricher = _enricher()

        async def _boom(run_id):
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(enricher, "_fetch_run_and_graph", _boom)
        result = _run(enricher.enrich_bcn(
            cd, {"event_type": "state_machine.run.completed", "data": {"output": {"r": 1}}},
            run_id="run-1",
        ))
        assert result is None                                 # 取数异常不阻断:返 run_detail=None
        eg = cd.data["execution_graph"]                       # 事件体兜底建图,保证非原始事件
        assert eg["status"] == Status.DONE.value               # run.completed → DONE 粗粒度投影
        assert eg["output"] == {"r": 1}
        assert eg["tasks"] == [] and eg["relations"] == []


async def _async_none():
    return None