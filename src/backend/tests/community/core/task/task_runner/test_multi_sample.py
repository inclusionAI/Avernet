from __future__ import annotations

import asyncio
import json

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_runner.multi_sample import MultiSampleExecutor


class _Context:
    def build(self, task_id, node_id):
        return {"mode": "execute"}


class _Formatter:
    def format_execute(self, context, node):
        assert context["skill_report_enabled"] is False
        return f"execute:{node.run_info.assignee}"


class _Sink:
    def __init__(self):
        self.reports = []

    async def report_result(self, data):
        self.reports.append(data)


def _node():
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        extend_props={"owner_bot_id": "owner", "owner_user_id": "u1"},
    )
    node = TaskNode(
        node_id="n1",
        task_id="t1",
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(background="bg", title="title"),
            goal=Goal(
                objective="choose best",
                acceptances=[AcceptanceCriteria(id="a1", description="quality")],
            ),
        ),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="bot-a:u1",
            extend_props={
                "dispatch_samples": [
                    {"sample_id": "sample-1", "rank": 1, "bot_id": "bot-a:u1"},
                    {"sample_id": "sample-2", "rank": 2, "bot_id": "bot-b:u2"},
                ]
            },
        ),
        node_run_graph=graph,
    )
    graph.tasks = [node]
    return node


def test_executes_all_samples_then_reports_llm_selected_result_once():
    class _Bot:
        def __init__(self):
            self.calls = []

        async def send_and_wait_async(
            self, *, bot_id, message, metadata=None, **kwargs
        ):
            self.calls.append((bot_id, message, metadata))
            if metadata["phase"] == "task_multi_sample_judge":
                return {
                    "status": "COMPLETED",
                    "result": {
                        "content": '{"selected_sample_id":"sample-2","reason":"better"}'
                    },
                }
            return {
                "status": "COMPLETED",
                "result": {
                    "content": json.dumps(
                        {
                            "success": True,
                            "data": f"output from {bot_id}",
                            "gaps": [],
                        }
                    )
                },
            }

    async def _case():
        bot = _Bot()
        sink = _Sink()
        executor = MultiSampleExecutor(
            bot=bot,
            graph=None,
            context=_Context(),
            formatter=_Formatter(),
            sink=sink,
        )
        await executor._run(_node())
        return bot, sink

    bot, sink = asyncio.run(_case())

    assert [call[0] for call in bot.calls[:2]] == ["bot-a:u1", "bot-b:u2"]
    assert bot.calls[2][0] == "owner:u1"
    assert len(sink.reports) == 1
    result = sink.reports[0].data["result"]
    assert result["data"] == "output from bot-b:u2"
    sampling = result["_ext_info"]["multi_sample"]
    assert sampling["selected_sample_id"] == "sample-2"
    assert sampling["judge"]["mode"] == "llm"


def test_malformed_judge_result_falls_back_to_highest_ranked_success():
    class _Bot:
        async def send_and_wait_async(self, *, bot_id, metadata=None, **kwargs):
            if metadata["phase"] == "task_multi_sample_judge":
                return {"status": "COMPLETED", "result": {"content": "not-json"}}
            return {
                "status": "COMPLETED",
                "result": {
                    "content": json.dumps({"success": True, "data": bot_id, "gaps": []})
                },
            }

    async def _case():
        sink = _Sink()
        executor = MultiSampleExecutor(
            bot=_Bot(),
            graph=None,
            context=_Context(),
            formatter=_Formatter(),
            sink=sink,
        )
        await executor._run(_node())
        return sink

    sink = asyncio.run(_case())
    result = sink.reports[0].data["result"]
    assert result["data"] == "bot-a:u1"
    assert result["_ext_info"]["multi_sample"]["judge"]["mode"] == "rank_fallback"


def test_failed_judge_run_with_valid_content_uses_ranked_fallback():
    class _Bot:
        async def send_and_wait_async(self, *, bot_id, metadata=None, **kwargs):
            if metadata["phase"] == "task_multi_sample_judge":
                return {
                    "status": "FAILED",
                    "result": {
                        "content": '{"selected_sample_id":"sample-2","reason":"stale"}'
                    },
                }
            return {
                "status": "COMPLETED",
                "result": {
                    "content": json.dumps({"success": True, "data": bot_id, "gaps": []})
                },
            }

    async def _case():
        sink = _Sink()
        executor = MultiSampleExecutor(
            bot=_Bot(),
            graph=None,
            context=_Context(),
            formatter=_Formatter(),
            sink=sink,
        )
        await executor._run(_node())
        return sink

    sink = asyncio.run(_case())
    result = sink.reports[0].data["result"]
    assert result["data"] == "bot-a:u1"
    assert result["_ext_info"]["multi_sample"]["judge"]["mode"] == "rank_fallback"


def test_selected_failed_acceptance_preserves_gaps_for_normal_replanning():
    class _Bot:
        async def send_and_wait_async(self, *, bot_id, metadata=None, **kwargs):
            if metadata["phase"] == "task_multi_sample_judge":
                return {
                    "status": "COMPLETED",
                    "result": {
                        "content": '{"selected_sample_id":"sample-2","reason":"closer"}'
                    },
                }
            return {
                "status": "COMPLETED",
                "result": {
                    "content": json.dumps(
                        {
                            "success": False,
                            "data": f"partial from {bot_id}",
                            "gaps": ["missing evidence"],
                        }
                    )
                },
            }

    async def _case():
        sink = _Sink()
        executor = MultiSampleExecutor(
            bot=_Bot(),
            graph=None,
            context=_Context(),
            formatter=_Formatter(),
            sink=sink,
        )
        await executor._run(_node())
        return sink

    sink = asyncio.run(_case())
    result = sink.reports[0].data["result"]
    assert result["success"] is False
    assert result["data"] == "partial from bot-b:u2"
    assert result["gaps"] == ["missing evidence"]


def test_all_invalid_sample_terminals_report_execution_error_without_judge():
    class _Bot:
        def __init__(self):
            self.phases = []

        async def send_and_wait_async(self, *, metadata=None, **kwargs):
            self.phases.append(metadata["phase"])
            return {"status": "COMPLETED", "result": {"content": "plain text"}}

    async def _case():
        bot = _Bot()
        sink = _Sink()
        executor = MultiSampleExecutor(
            bot=bot,
            graph=None,
            context=_Context(),
            formatter=_Formatter(),
            sink=sink,
        )
        await executor._run(_node())
        return bot, sink

    bot, sink = asyncio.run(_case())
    assert bot.phases == ["task_multi_sample", "task_multi_sample"]
    result = sink.reports[0].data["result"]
    assert result["success"] is False
    assert result["exec_error"] == "multi_sample_all_failed"
