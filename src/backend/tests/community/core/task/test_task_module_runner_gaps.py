from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.task.domain.models import (
    Context, Goal, RuntimeInfo, Status, TaskCallbackData, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.callback_adapter import (
    _patch_extend_props_with_origin,
)
from agentclaw.community.core.task.task_runner.client.bcs_bot_identity_resolver import (
    BotServiceBcsBotIdentityResolver,
)
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    search_candidates,
    search_tokens,
)
from agentclaw.community.core.task.task_runner.client.catalog_keyword_discover import (
    CatalogKeywordBotDiscover,
)
from agentclaw.community.core.task.task_runner.client.translators import (
    BcsSessionTranslator,
)
from agentclaw.community.core.task.task_runner.event_dispatcher import (
    TaskSemanticEventDispatcher,
)
from agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor import (
    _relay_claim_instruction,
    _relay_task_inputs,
    _emit_bbs_trajectory,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
    TaskExecutorResultPoller,
)


def test_callback_candidate_identity_and_duplicate_edges():
    assert _patch_extend_props_with_origin({"x": 1}, None) == {"x": 1}
    assert search_tokens("same same") == ["same"]

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {
                "items": [
                    "bad",
                    {"bot_uuid": "b1", "recommend": {"score": 1}},
                    {"bot_uuid": "b1", "recommend": {"score": 0}},
                ]
            }

    result = asyncio.run(search_candidates(_Discover(), "one"))
    assert [item["bot_uuid"] for item in result.candidates] == ["b1"]
    assert result.keyword_hits[0].bot_ids == ("b1",)


def test_identity_resolver_skips_malformed_and_unrequested_items():
    service = SimpleNamespace(
        list_bots_by_conditions=lambda **_kwargs: {
            "items": [
                "bad",
                {"bot_id": "other", "owner_id": "u0"},
                {"bot_id": "b1", "owner_id": "u1"},
            ]
        }
    )
    assert BotServiceBcsBotIdentityResolver(service).resolve_many(["b1"]) == {
        "b1": "b1:u1"
    }


def test_catalog_existing_recommend_score_and_non_dict_recommend():
    discover = CatalogKeywordBotDiscover(SimpleNamespace())
    discover._query = lambda **_kwargs: [
        {"bot_id": "b1", "recommend": {"score": 0.2}},
        {"bot_id": "b2", "recommend": "bad"},
    ]
    result = discover.search_by_keyword(keyword="x", user_id="u1")
    assert result["items"][0]["recommend"] == {"score": 0.2}
    assert result["items"][1]["recommend"]["score"] == 0.95


def test_bcs_session_translator_skips_non_assistant_message():
    result = BcsSessionTranslator.adapt(
        {"session": {"status": "completed", "output": None}},
        [
            {"role": "assistant", "content": '{"success": true, "data": "ok"}'},
            {"role": "user", "content": "ignored"},
        ],
        "t1::n1",
    )
    assert result.data["result"]["success"] is True


def test_event_dispatcher_zero_limit_exits_without_query():
    graph = SimpleNamespace(list_pending_semantic_events=MagicMock())
    dispatcher = TaskSemanticEventDispatcher(graph)
    assert asyncio.run(dispatcher.dispatch_pending("t1", limit=0)) == 0
    graph.list_pending_semantic_events.assert_not_called()


def test_bbs_helpers_optional_reason_and_claim_instruction_branches():
    trajectory = SimpleNamespace(emit_trajectory_event=MagicMock())
    _emit_bbs_trajectory(
        trajectory,
        SimpleNamespace(task_id="t1", tasks=[]),
        "n1",
        action_result="failed",
        exception=RuntimeError("boom"),
    )
    kwargs = trajectory.emit_trajectory_event.call_args.kwargs
    assert kwargs["error_type"] == "unclassified"

    assert _relay_claim_instruction(title="", goal="", reason="") == ""
    assert _relay_claim_instruction(title="", goal="", reason="why") == (
        "BBS认领依据: why"
    )

    node = TaskNode(
        node_id="n1", task_id="t1", status=Status.PENDING,
        task_spec=TaskSpec(context=Context(title="title", background="bg"), goal=Goal(objective="goal", acceptances=[])),
        run_info=RuntimeInfo(), node_run_graph=None,
    )
    inputs = _relay_task_inputs(
        execution_graph=SimpleNamespace(tasks=[], relations=[], extend_props={}),
        node=node,
        title="",
        goal="",
        reason="",
    )
    assert inputs["instruction"]


def test_task_executor_dispatch_input_serialization_failure():
    class _BadString:
        def __str__(self):
            raise RuntimeError("cannot stringify")

    node = TaskNode(
        node_id="n1", task_id="t1", status=Status.PENDING,
        task_spec=TaskSpec(context=Context(title="title", background="bg"), goal=Goal(objective="goal", acceptances=[])),
        run_info=RuntimeInfo(), node_run_graph=None,
    )
    executor = TaskExecutor.__new__(TaskExecutor)
    executor._graph = object()
    executor._report_node_patch = MagicMock()
    executor._persist_dispatch_ids(node, exec_request_input=_BadString())
    patch = executor._report_node_patch.call_args.args[0]
    assert "_exec_request_input" not in patch.extend_props_patch


def test_poller_report_without_sink_absent_handle_and_explicit_stop_event():
    poller = TaskExecutorResultPoller(bot=SimpleNamespace(), bcs=SimpleNamespace())
    handle = object()
    asyncio.run(poller._report(TaskCallbackData(data={}), handle))

    stop = threading.Event()
    stop.set()
    poller.run_poll_loop(stop)
