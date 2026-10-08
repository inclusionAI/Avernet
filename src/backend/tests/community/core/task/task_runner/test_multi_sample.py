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


def _executor(*, bot=None, graph=None):
    class _UnusedBot:
        async def send_and_wait_async(self, **kwargs):
            raise AssertionError(f"unexpected bot call: {kwargs}")

    return MultiSampleExecutor(
        bot=bot or _UnusedBot(),
        graph=graph,
        context=_Context(),
        formatter=_Formatter(),
        sink=_Sink(),
    )


def test_start_rejects_non_multi_sample_and_tracks_successful_background_task():
    async def _case():
        executor = _executor()
        node = _node()
        node.run_info.extend_props["dispatch_samples"] = []
        assert executor.start(node) is False

        node.run_info.extend_props["dispatch_samples"] = [{}, {}]
        completed = asyncio.Event()

        async def _run(_node):
            completed.set()

        executor._run = _run
        assert executor.start(node) is True
        await completed.wait()
        await asyncio.sleep(0)
        assert executor._tasks == set()

    asyncio.run(_case())


def test_on_done_logs_background_exception(caplog):
    async def _case():
        executor = _executor()

        async def _fail():
            raise RuntimeError("background boom")

        task = asyncio.create_task(_fail())
        executor._tasks.add(task)
        try:
            await task
        except RuntimeError:
            pass
        executor._on_done(task)
        assert task not in executor._tasks

    asyncio.run(_case())
    assert "background execution crashed" in caplog.text


def test_wait_until_running_handles_transition_query_failure_and_exhaustion(monkeypatch):
    class _TransitionGraph:
        def __init__(self):
            self.calls = 0

        def query_task_nodes(self, task_id, criteria):
            assert task_id == "t1"
            assert criteria.node_ids == ["n1"]
            self.calls += 1
            node = _node()
            node.status = Status.RUNNING if self.calls == 2 else Status.PENDING
            return [node]

    class _FailingGraph:
        def query_task_nodes(self, task_id, criteria):
            raise RuntimeError("query unavailable")

    class _PendingGraph:
        def __init__(self):
            self.calls = 0

        def query_task_nodes(self, task_id, criteria):
            self.calls += 1
            return []

    sleeps = 0

    async def _no_sleep(_delay):
        nonlocal sleeps
        sleeps += 1

    monkeypatch.setattr(
        "agentclaw.community.core.task.task_runner.multi_sample.asyncio.sleep",
        _no_sleep,
    )

    async def _case():
        transition = _TransitionGraph()
        await _executor(graph=transition)._wait_until_running(_node())
        assert transition.calls == 2

        await _executor(graph=_FailingGraph())._wait_until_running(_node())

        pending = _PendingGraph()
        await _executor(graph=pending)._wait_until_running(_node())
        assert pending.calls == 200

    asyncio.run(_case())
    assert sleeps == 201


def test_execute_sample_converts_exception_to_failed_result():
    class _Bot:
        async def send_and_wait_async(self, **kwargs):
            raise LookupError("bot unavailable")

    result = asyncio.run(
        _executor(bot=_Bot())._execute_sample(
            _node(), {"sample_id": "sample-x", "bot_id": "bot-x", "rank": 7}
        )
    )

    assert result == {
        "sample_id": "sample-x",
        "bot_id": "bot-x",
        "rank": 7,
        "completed": False,
        "success": False,
        "output": None,
        "gaps": [],
        "error": "LookupError: bot unavailable",
    }


def test_select_best_falls_back_when_owner_missing_or_judge_selects_unknown_sample():
    successful = [
        {
            "sample_id": "sample-1",
            "bot_id": "bot-a",
            "rank": 1,
            "completed": True,
            "success": True,
            "output": "first",
            "gaps": [],
            "error": None,
        },
        {
            "sample_id": "sample-2",
            "bot_id": "bot-b",
            "rank": 2,
            "completed": True,
            "success": True,
            "output": "second",
            "gaps": [],
            "error": None,
        },
    ]

    class _UnknownJudgeBot:
        async def send_and_wait_async(self, **kwargs):
            return {
                "status": "COMPLETED",
                "result": {
                    "content": '{"selected_sample_id":"missing","reason":"bad"}'
                },
            }

    async def _case():
        ownerless = _node()
        ownerless.node_run_graph.extend_props = {}
        selected, meta = await _executor()._select_best(ownerless, successful)
        assert selected["sample_id"] == "sample-1"
        assert meta == {"mode": "rank_fallback", "reason": "owner_missing"}

        selected, meta = await _executor(bot=_UnknownJudgeBot())._select_best(
            _node(), successful
        )
        assert selected["sample_id"] == "sample-1"
        assert meta["mode"] == "rank_fallback"
        assert "unknown sample" in meta["reason"]

    asyncio.run(_case())


def test_owner_bot_id_uses_dashboard_fallback_and_tolerates_query_failure():
    class _Snapshot:
        extend_props = {"owner_bot_id": "dashboard-owner", "owner_user_id": "u9"}

    class _DashboardGraph:
        def query_task_dashboard(self, task_id):
            assert task_id == "t1"
            return _Snapshot()

    class _FailingGraph:
        def query_task_dashboard(self, task_id):
            raise RuntimeError("dashboard unavailable")

    node = _node()
    node.node_run_graph.extend_props = {}

    assert _executor(graph=_DashboardGraph())._owner_bot_id(node) == "dashboard-owner:u9"
    assert _executor(graph=_FailingGraph())._owner_bot_id(node) == ""


def test_task_executor_dispatch_routes_multi_sample_and_closes_without_poller():
    from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
        TaskExecutor,
    )

    executor = TaskExecutor(
        bot=None,
        bcs=None,
        formatter=None,
        context=None,
        sink=None,
        poller=None,
    )
    node = _node()
    calls = []

    class _MultiSample:
        def can_handle(self, candidate):
            calls.append(("can_handle", candidate.node_id))
            return True

        def start(self, candidate):
            calls.append(("start", candidate.node_id))
            return True

    executor._multi_sample = _MultiSample()

    async def _case():
        assert await executor.dispatch([node]) == [True]
        executor._persist_dispatch_ids(node)
        await executor.aclose()

    asyncio.run(_case())
    assert calls == [("can_handle", "n1"), ("start", "n1")]
