"""extract_json + 两个策略解析器(plan/dispatch)对 skill 回投散文/代码块包裹的鲁棒解析单测。

覆盖:裸 JSON 快路径、```json 代码块、散文包裹、字符串内括号不误判、非字符串透传、
无可解析 JSON 抛 ValueError;接线后 _parse_children/_parse_search_result 能从真实 bot 输出解析。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)


class TestExtractJsonClean:
    def test_clean_list(self):
        assert extract_json('[{"a":1}]') == [{"a": 1}]

    def test_clean_object(self):
        assert extract_json('{"outcome":"MISS"}') == {"outcome": "MISS"}

    def test_scalar(self):
        assert extract_json("42") == 42

    def test_empty_array_gap_closed(self):
        assert extract_json("[]") == []


class TestExtractJsonFenced:
    def test_json_fence_with_prose(self):
        c = "根据剧本,需返回子节点 N_overview。\n\n```json\n[{\"metadata\":{\"task_id\":\"N_overview\"}}]\n```"
        assert extract_json(c) == [{"metadata": {"task_id": "N_overview"}}]

    def test_bare_fence_no_lang(self):
        assert extract_json('```\n{"outcome":"MISS","miss_reason":"x"}\n```') == {"outcome": "MISS", "miss_reason": "x"}

    def test_fence_followed_by_text(self):
        c = "思考如下。\n```json\n{\"outcome\":\"HIT_SINGLE\",\"bot_id\":\"b1\"}\n```\n完成"
        assert extract_json(c) == {"outcome": "HIT_SINGLE", "bot_id": "b1"}

    def test_multiple_fences_returns_first_parseable(self):
        c = "```json\n{\"a\":1}\n```\n文字\n```json\n{\"b\":2}\n```"
        assert extract_json(c) == {"a": 1}


class TestExtractJsonProseWrapped:
    def test_no_fence_balanced_scan(self):
        assert extract_json("结果 [{\"x\":2}] 收尾文字") == [{"x": 2}]

    def test_object_no_fence_balanced(self):
        assert extract_json("前缀 {\"outcome\":\"HIT_GROUP\",\"group_id\":\"g1\"} 后缀") == {"outcome": "HIT_GROUP", "group_id": "g1"}


class TestExtractJsonStringsContainBrackets:
    def test_braces_inside_string_not_miscounted(self):
        out = extract_json('```json\n{"a":"}{","b":[1,2]}\n```')
        assert out == {"a": "}{", "b": [1, 2]}

    def test_brackets_inside_string_not_miscounted(self):
        out = extract_json("前缀 [{\"c\":\"[1,2]\",\"d\":\"3]\"}] 后缀")
        assert out == [{"c": "[1,2]", "d": "3]"}]


class TestExtractJsonEdgeCases:
    def test_non_str_passthrough(self):
        assert extract_json(None) is None
        assert extract_json([{"already": 1}]) == [{"already": 1}]

    def test_no_parseable_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("no json here at all")

    def test_malformed_brackets_raises(self):
        with pytest.raises(ValueError):
            extract_json("随机 [不闭合 文字")


# ---------------- 策略解析器接线验证 ----------------

def _graph() -> TaskExecutionGraph:
    g = TaskExecutionGraph(loop_round=0, status=Status.PENDING, run_id="r", output={},
                           tasks=[], relations=[], extend_props={})
    g.tasks.append(TaskNode(node_id="t_case", task_id="t_case", status=Status.PENDING,
        task_spec=TaskSpec(metadata=Metadata(task_id="t_case", title="T", instruction="i"),
                           context=Context(background="bg"),
                           goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")])),
        run_info=RuntimeInfo(), node_run_graph=None))  # type: ignore[arg-type]
    return g


class TestPlanParserWiring:
    def test_parses_prose_fence_into_n_overview(self):
        from agentclaw.community.core.task.task_plan.strategies import _parse_plan_result
        g = _graph()
        run = {"status": "COMPLETED",
               "result": {"content": "根据剧本,返回 N_overview。\n```json\n[{\"metadata\": {\"task_id\": \"N_overview\", \"title\": \"存储行业概览\", \"instruction\": \"撰写概览\"}, \"context\": {\"background\": \"bg\", \"extend_props\": {}}, \"goal\": {\"objective\": \"o\", \"acceptances\": [{\"id\": \"ac_overview\", \"description\": \"d\"}]}}]\n```"}}
        pr = _parse_plan_result(run, g.tasks[0], g)
        assert [k.node_id for k in pr.children] == ["N_overview"]
        assert pr.children[0].task_spec.metadata.title == "存储行业概览"

    def test_clean_empty_array_backward_compat(self):
        from agentclaw.community.core.task.task_plan.strategies import _parse_plan_result
        g = _graph()
        assert _parse_plan_result({"status": "COMPLETED", "result": {"content": "[]"}}, g.tasks[0], g).children == []

    def test_unparseable_returns_empty(self):
        from agentclaw.community.core.task.task_plan.strategies import _parse_plan_result
        g = _graph()
        assert _parse_plan_result({"status": "COMPLETED", "result": {"content": "纯散文无 json"}}, g.tasks[0], g).children == []


class TestDispatchParserWiring:
    def test_parses_prose_fence_hit_single(self):
        from agentclaw.community.core.task.task_dispatch.strategies import _parse_search_result, SearchOutcome
        run = {"status": "COMPLETED",
               "result": {"content": "分析后决出:\n```json\n{\"outcome\":\"HIT_SINGLE\",\"bot_id\":\"行业信息抓取Bot\",\"bot_name\":\"行业信息抓取Bot\",\"owner_id\":\"146836\",\"owner_name\":\"栖真\"}\n```\n完毕"}}
        sr = _parse_search_result(run)
        assert sr.outcome == SearchOutcome.HIT_SINGLE
        assert sr.bot_id == "行业信息抓取Bot"
        assert sr.bot_name == "行业信息抓取Bot"
        assert sr.owner_id == "146836"
        assert sr.owner_name == "栖真"

    def test_parses_multi_bots_formation(self):
        from agentclaw.community.core.task.task_dispatch.strategies import _parse_search_result, SearchOutcome
        run = {"status": "COMPLETED", "result": {"content": "```json\n{\"outcome\":\"HIT_MULTI_BOTS\",\"bot_ids\":[\"aBot\",\"bBot\"],\"collab_mode\":\"manager_worker\",\"group_name\":\"G\",\"manager_bot_id\":\"aBot\"}\n```"}}
        sr = _parse_search_result(run)
        assert sr.outcome == SearchOutcome.HIT_MULTI_BOTS
        assert sr.group_formation.bot_ids == ["aBot", "bBot"]
        assert sr.group_formation.extend_props["manager_bot_id"] == "aBot"

    def test_unparseable_returns_miss_parse_error(self):
        from agentclaw.community.core.task.task_dispatch.strategies import _parse_search_result, SearchOutcome
        sr = _parse_search_result({"status": "COMPLETED", "result": {"content": "prose no json"}})
        assert sr.outcome == SearchOutcome.MISS
        assert sr.miss_reason == "parse_error"
