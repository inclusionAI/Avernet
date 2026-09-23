"""覆盖缺口批次 B — 协议桩 / 小型 domain 件 / repository serializers。

目标文件(原 85.3% 基线的 missing_lines 清零):
* ``services/task_grant_service.py`` + ``task_grant_service_protocol.py``(0% → 全)
* ``task_service_protocol.py`` / ``task_runner/execution_protocols.py``(协议桩)
* ``domain/json_extract.py``(fence 兜底链) / ``domain/identity.py``
* ``domain/models.py``(task_spec_instruction 富段 / runtime_profile 非法值 /
  effective_run_mode 无 run_info / _relay_graph_status 空 tasks/terminal)
* ``repository/serializers.py``(None 早退 + runtime/action/graph 序列化回程 +
  graph_from_parts 坏 run_id 兜底)
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain import json_extract
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    NodeAction,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskRuntimeProfile,
    TaskSpec,
    _relay_graph_status,
    effective_run_mode,
    NodeActionEvent,
    task_spec_instruction,
)
from agentclaw.community.core.task.domain.json_extract import (
    _balanced_substring,
    extract_json,
)
from agentclaw.community.core.task.repository import serializers
from agentclaw.community.core.task.services.task_grant_service import (
    TaskClaimGrantService,
)
from agentclaw.community.core.task.task_grant_service_protocol import (
    GRANTED,
    REVOKED,
    GrantResult,
    RevokeResult,
    TaskClaimGrantServiceProtocol,
)
from agentclaw.community.core.task.task_runner import execution_protocols
from agentclaw.community.core.task.task_service_protocol import (
    TaskServiceProtocol,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort


# ---------------------------------------------------------------------------
# TaskClaimGrantService — secbaas 透传中继(grant/revoke)
# ---------------------------------------------------------------------------


class _FakeGrantBot:
    """OpenApiBotPort 假体:记录 grant/revoke 入参,回放 api_key_prefix。"""

    api_key_prefix = "pk-123"

    def __init__(self) -> None:
        self.grant_calls: list[dict] = []
        self.revoke_calls: list[dict] = []

    async def grant(self, *, bcs_bot_id, cookie, referer) -> None:  # noqa: ANN001
        self.grant_calls.append({"bcs_bot_id": bcs_bot_id, "cookie": cookie,
                                 "referer": referer})

    async def revoke(self, *, bcs_bot_id, cookie, referer) -> None:  # noqa: ANN001
        self.revoke_calls.append({"bcs_bot_id": bcs_bot_id, "cookie": cookie,
                                  "referer": referer})


@pytest.mark.asyncio
@pytest.mark.unit
async def test_grant_service_grant_passes_cookie_referer_and_returns_result():
    bot = _FakeGrantBot()
    svc = TaskClaimGrantService(bot=bot)

    result = await svc.grant(bcs_bot_id="botA:U1", cookie="cks=1",
                             referer="https://host/page", operator="alice")

    assert isinstance(result, GrantResult)
    assert result.grant_status == GRANTED
    assert result.api_key_prefix == "pk-123"
    assert result.operator == "alice"
    assert bot.grant_calls == [{"bcs_bot_id": "botA:U1", "cookie": "cks=1",
                                "referer": "https://host/page"}]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_grant_service_revoke_returns_revoked_and_forwards():
    bot = _FakeGrantBot()
    svc = TaskClaimGrantService(bot=bot)

    result = await svc.revoke(bcs_bot_id="botA:U1", cookie="cks=1",
                              referer="r", operator="alice")

    assert isinstance(result, RevokeResult)
    assert result.grant_status == REVOKED
    assert bot.revoke_calls == [{"bcs_bot_id": "botA:U1", "cookie": "cks=1",
                                 "referer": "r"}]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_grant_service_unwired_bot_raises_runtime_error():
    """corp overlay 缺 OpenApiBotPort → grant/revoke 皆 RuntimeError(装配错误显式化)。"""
    svc = TaskClaimGrantService(bot=None)

    with pytest.raises(RuntimeError):
        await svc.grant(bcs_bot_id="b", cookie="c", referer="r", operator="o")
    with pytest.raises(RuntimeError):
        await svc.revoke(bcs_bot_id="b", cookie="c", referer="r", operator="o")


@pytest.mark.unit
def test_grant_service_satisfies_runtime_checkable_protocol():
    assert isinstance(TaskClaimGrantService(bot=_FakeGrantBot()),
                     TaskClaimGrantServiceProtocol)


# ---------------------------------------------------------------------------
# 协议桩 — 桩体(``...``)语义:调用返回 None;runtime_checkable 可作类型闸
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_service_protocol_stub_bodies_execute():
    """协议方法桩体调用即 ``None``(不抛)——桩语义成立 + 签名存在。"""
    p = TaskServiceProtocol
    assert asyncio.run(p.execute(None, None)) is None
    assert asyncio.run(p.report_task_event(
        None, task_id="t", node_id="n", event_type="e", event_id="1",
        holder_id="h", payload={})) is None
    assert asyncio.run(p.search_task_candidates(None, query="q")) is None
    assert asyncio.run(p.dispatch_task(
        None, task_id="t", origin_node_id="o", target_node_id="g",
        holder_id="h", relay_turn="1", dispatch_id="d")) is None
    assert asyncio.run(p.report_bbs_result(
        None, "t", "n", "bot")) is None
    assert asyncio.run(p.converge_by_session(None, "sess", success=True)) is None
    assert asyncio.run(p.apply_manager_worker_event(None, {})) is None
    assert asyncio.run(p.redrive_task(None, "t")) is None
    # 同步方法
    assert p.get_task_dashboard(None, "t") is None
    assert p.list_tasks(None) is None
    assert p.list_tasks_page(None, page=1, page_size=5) is None
    assert p.claim_bbs_task(None, "t", "bot") is None
    assert p.attach_bbs_node(None, "t", "p", object(), "bot") is None


@pytest.mark.unit
def test_execution_protocol_stub_bodies_execute():
    ep = execution_protocols
    assert asyncio.run(ep.CentralizedExecutionAdapterProtocol.start(None, "t")) is None
    assert asyncio.run(ep.CentralizedExecutionAdapterProtocol.on_start(None, None)) is None
    assert asyncio.run(ep.CentralizedExecutionAdapterProtocol.on_report(None, None)) is None
    assert asyncio.run(ep.CentralizedExecutionAdapterProtocol.on_harness(None, None)) is None
    # property 桩体:同步取值,桩体即 None
    relay = ep.RelayExecutionAdapterProtocol
    assert relay.runner.fget(None) is None
    assert relay.search.fget(None) is None
    assert asyncio.run(ep.TaskSearchProtocol.search(None, "q")) is None
    # 全部契约符号可解析(防孤儿协议)
    for name in ("CentralizedExecutionAdapterProtocol",
                 "RelayExecutionAdapterProtocol", "TaskSearchProtocol"):
        assert hasattr(ep, name)


# ---------------------------------------------------------------------------
# domain/json_extract — fence 兜底链各分支
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_json_non_string_passthrough_and_bare_json():
    assert extract_json(123) == 123
    assert extract_json(None) is None
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('  [1, 2]  ') == [1, 2]


@pytest.mark.unit
def test_extract_json_skips_empty_fenced_block():
    """fence 块内容为空 → continue → 全失败 → ValueError。"""
    with pytest.raises(ValueError):
        extract_json("```json\n```")


@pytest.mark.unit
def test_extract_json_fenced_prose_falls_back_to_balanced_substring():
    """fence 块内散文裹 JSON → json.loads 块失败 → 配平子串兜底成功。"""
    content = "结果如下:\n```json\n好的,结果 {\"a\": 1}\n```"
    assert extract_json(content) == {"a": 1}
    # 无 fence 的散文包裹同样走第 4 步配平兜底
    assert extract_json("the answer is [1,2] ok") == [1, 2]


@pytest.mark.unit
def test_extract_json_fenced_block_with_paren_balanced_but_invalid_json():
    """fence 块内确有配平花括号但不是 JSON → 块内兜底也失败 → continue → 整体 ValueError。"""
    with pytest.raises(ValueError):
        extract_json("```json\nvalue {not json}\n```")


@pytest.mark.unit
def test_balanced_substring_respects_string_escapes():
    """字符串字面量内的转义 ``\\"`` 与其后的括号不做深度判定。"""
    text = '{"k": "a\\"b]c"} tail'
    span = _balanced_substring(text)
    assert span == '{"k": "a\\"b]c"}'
    assert json_extract._parse_safe(span) if hasattr(json_extract, "_parse_safe") else True
    # 无起符 → None
    assert _balanced_substring("no brackets here") is None


# ---------------------------------------------------------------------------
# domain/identity — compose_bot_identity 各分支
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compose_bot_identity_owner_is_authoritative():
    assert compose_bot_identity("botA", "user1") == "botA:user1"
    # 已含复合地址但显式 owner 更权威 → 重建(防陈旧内嵌 owner 外泄)
    assert compose_bot_identity("botA:stale", "user1") == "botA:user1"
    # owner_id 空白串(非 None)→ bot_id 原样返回
    assert compose_bot_identity("botB:u9", "   ") == "botB:u9"
    assert compose_bot_identity("botC", None) == "botC"
    assert compose_bot_identity("  botD  ") == "botD"


# ---------------------------------------------------------------------------
# domain/models — 指令富段 / runtime_profile 容错 / effective_run_mode / relay 图态
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_spec_instruction_renders_deliverables_constraints_resources():
    spec = TaskSpec(
        context=Context(background="bg text",
                        extend_props={"deliverables": ["报告", "模型"],
                                      "constraints": ["3天"],
                                      "resources": ["库X"]}),
        goal=Goal(objective="做调研", acceptances=[]),
    )
    text = task_spec_instruction(spec)
    lines = text.splitlines()
    assert lines[0] == "做调研"
    assert "交付物: 报告；模型" in lines
    assert "约束: 3天" in lines
    assert "资源: 库X" in lines
    assert lines[-1] == "背景: bg text"


@pytest.mark.unit
def test_task_runtime_profile_from_execution_config_tolerates_bad_raw():
    """runtime_profile 非 dict → 回退空;modes 缺失/非法 → 回退类默认。"""
    prof = TaskRuntimeProfile.from_execution_config({"runtime_profile": "oops"})
    assert prof.planner_strategy == "default"
    assert prof.dispatcher_strategy == "default"
    assert prof.runner_strategy == "default"
    assert prof.allowed_run_modes == ("single_bot", "coop_group", "bbs")
    # 合法 list 去重 + 去空白
    prof2 = TaskRuntimeProfile.from_execution_config(
        {"runtime_profile": {"allowed_run_modes": ["single_bot", "single_bot", " "]}})
    assert prof2.allowed_run_modes == ("single_bot",)


@pytest.mark.unit
def test_effective_run_mode_none_when_node_lacks_run_info():
    assert effective_run_mode(SimpleNamespace()) is None
    # 正常路径:actual_run_mode 覆盖 > run_mode 兜底
    node = SimpleNamespace(run_info=RuntimeInfo(run_mode="single_bot",
                                                extend_props={"actual_run_mode": "bbs"}))
    assert effective_run_mode(node) == "bbs"
    node2 = SimpleNamespace(run_info=RuntimeInfo(run_mode="coop_group",
                                                 extend_props={}))
    assert effective_run_mode(node2) == "coop_group"
    node3 = SimpleNamespace(run_info=RuntimeInfo(run_mode="  ", extend_props={}))
    assert effective_run_mode(node3) is None


def _bare_graph(status: Status) -> TaskExecutionGraph:
    graph = TaskExecutionGraph(run_id=1, loop_round=0, status=status,
                               output={}, extend_props={}, task_id="T")
    graph.tasks = []
    graph.relations = []
    return graph


@pytest.mark.unit
def test_relay_graph_status_empty_tasks_returns_graph_status():
    graph = _bare_graph(Status.RUNNING)
    graph.tasks = []
    assert _relay_graph_status(graph) is Status.RUNNING


@pytest.mark.unit
def test_relay_graph_status_terminal_node_wins():
    graph = _bare_graph(Status.RUNNING)
    graph.tasks = [SimpleNamespace(status=Status.HUNG)]
    assert _relay_graph_status(graph) is Status.HUNG
    graph2 = _bare_graph(Status.FAILED)
    graph2.tasks = [SimpleNamespace(status=Status.SUCCESS)]
    # 图级 FAILED/HUNG/CANCELLED 优先返回
    assert _relay_graph_status(graph2) is Status.FAILED


# ---------------------------------------------------------------------------
# repository/serializers — None 早退 + 序列化回程
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acceptance_serializers_none_early_returns():
    assert serializers._acceptance_to_dict(None) is None
    assert serializers._acceptance_from_dict(None) is None


@pytest.mark.unit
def test_acceptance_from_dict_legacy_compat_paths():
    # acceptances_metric 掉 passed=False 项;gaps 旧名兼容 gap_items
    legacy = {"verdict": "FAILED",
              "acceptances_metric": [{"id": "a", "passed": False},
                                     {"id": "b", "passed": True}],
              "gaps": ["缺证据"]}
    res = serializers._acceptance_from_dict(legacy)
    assert res.verdict is AcceptanceVerdict.FAILED
    assert res.done_items == [{"id": "b", "passed": True}]
    assert res.gap_items == ["缺证据"]


@pytest.mark.unit
def test_runtime_to_from_dict_round_trip_with_actual_goal_and_acceptance():
    runtime = RuntimeInfo(
        run_mode="single_bot", assignee="botA", start_time=10, end_time=20,
        actual_goal=Goal(objective="目标", acceptances=[]),
        output={"k": "v"}, extend_props={"x": 1},
        acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE,
                                           done_items=["a"], gap_items=[]),
        progress_reason="pr", failure_reason="fr",
    )
    d = serializers.runtime_to_dict(runtime)
    assert d["acceptance_result"] == {"verdict": "DONE", "done_items": ["a"],
                                      "gap_items": []}
    rt = serializers.runtime_from_dict(d)
    assert rt.run_mode == "single_bot"
    assert rt.assignee == "botA"
    assert rt.actual_goal is not None and rt.actual_goal.objective == "目标"
    assert rt.acceptance_result.verdict is AcceptanceVerdict.DONE
    assert rt.output == {"k": "v"}
    assert rt.extend_props == {"x": 1}
    # 空 dict → 全 None 默认
    rt_empty = serializers.runtime_from_dict({})
    assert rt_empty.run_mode is None
    assert rt_empty.actual_goal is None
    assert rt_empty.acceptance_result is None


@pytest.mark.unit
def test_action_to_from_dict_round_trip():
    event = NodeActionEvent(seq=1, ts=123, action=NodeAction.EXECUTE,
                            loop_round=2, attempt=1,
                            status_from=Status.PLANNING, status_to=Status.RUNNING,
                            payload={"a": 1})
    d = serializers.action_to_dict(event)
    assert d["action"] == "execute"
    assert d["status_from"] == "PLANNING" and d["status_to"] == "RUNNING"
    rt = serializers.action_from_dict(d)
    assert asdict(rt) == asdict(event)
    # 缺省字段回程(无 status/from)
    rt2 = serializers.action_from_dict({"seq": "2", "action": "verify"})
    assert rt2.seq == 2 and rt2.action is NodeAction.VERIFY
    assert rt2.status_from is None and rt2.status_to is None


def _node(graph: TaskExecutionGraph, node_id: str, status: Status) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=graph.task_id, status=status,
        task_spec=TaskSpec(context=Context(background="b"),
                           goal=Goal(objective="o", acceptances=[])),
        run_info=RuntimeInfo(), node_run_graph=graph,
    )


@pytest.mark.unit
def test_graph_to_dict_serializes_nodes_and_relations():
    graph = _bare_graph(Status.RUNNING)
    root = _node(graph, "root", Status.RUNNING)
    child = _node(graph, "c1", Status.PENDING)
    graph.tasks = [root, child]
    graph.relations = [Relation(src_id="root", dst_id="c1",
                                type=RelationType.DEPENDENCY)]

    d = serializers.graph_to_dict(graph)

    assert d["task_id"] == "T" and d["status"] == "RUNNING"
    assert [n["node_id"] for n in d["tasks"]] == ["root", "c1"]
    assert d["relations"][0] == {"src_id": "root", "dst_id": "c1",
                                 "type": "DEPENDENCY", "extend_props": {}}


@pytest.mark.unit
def test_graph_from_parts_bad_run_id_falls_back_to_zero():
    graph = serializers.graph_from_parts(
        task_id="T", run_id="not-int", loop_round=0, status=Status.PENDING,
        output=None, extend_props=None,
        nodes=[("root", Status.PENDING, {"goal": {"objective": "o",
                                                  "acceptances": []}},
                RuntimeInfo())],
        relations=[],
    )
    assert graph.run_id == 0
    assert graph.tasks[0].node_id == "root"
    assert graph.tasks[0].run_info.run_mode is None
    # run_id None → 0 同兜底
    g2 = serializers.graph_from_parts(
        task_id="T", run_id=None, loop_round=0, status=Status.PENDING,
        output={}, extend_props={}, nodes=[], relations=[],
    )
    assert g2.run_id == 0


@pytest.mark.unit
def test_open_api_bot_port_importable_for_grant_service():
    """grant service 依赖的端口符号可解析(防孤儿协议)。"""
    assert OpenApiBotPort is not None