from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from agentclaw.community.core.task.domain.models import Status, TaskCallbackData
from agentclaw.community.core.task.task_runner.client import callback_data_enricher as mod


def _run(coro):
    return asyncio.run(coro)


def _callback(data=None):
    return TaskCallbackData(data={} if data is None else data)


def test_json_parsers_and_timestamp_conversion_cover_all_input_shapes():
    marker = {"a": 1}
    assert mod._parse_json(marker) is marker
    assert mod._parse_json('{"a": 1}') == marker
    assert mod._parse_json("not-json", "fallback") == "fallback"
    assert mod._parse_json(42, "fallback") == "fallback"
    assert mod._parse_dict('["not", "a", "dict"]') == {}
    assert mod._parse_dict(None) == {}

    assert mod._parse_dict_strict(None, field="x") == {}
    assert mod._parse_dict_strict("  ", field="x") == {}
    assert mod._parse_dict_strict(marker, field="x") is marker
    assert mod._parse_dict_strict('["x"]', field="x") == {}
    assert mod._parse_dict_strict(42, field="x") == {}
    with pytest.raises(ValueError, match="field=broken"):
        mod._parse_dict_strict("{", field="broken")

    assert mod._to_ms(None) is None
    assert mod._to_ms("bad") is None
    assert mod._to_ms(123) == 123_000
    assert mod._to_ms(1_700_000_000_000) == 1_700_000_000_000


def test_build_claw_mind_graph_preserves_rich_fields_and_filters_relations():
    ext = {
        "flow_runs": {
            "id": 12,
            "workflow_id": "wf",
            "workflow_title": "Workflow",
            "origin_session_id": "session",
            "total_duration_ms": 99,
            "started_at": 123,
            "params_json": '{"topic": "coverage"}',
            "result_json": '{"answer": 42}',
        },
        "node_executions": [
            None,
            {},
            {
                "node_id": "parent",
                "status": "completed",
                "output_json": "{}",
            },
            {
                "node_id": "child",
                "node_title": "Child title",
                "status": "failed",
                "executor_type": "bot",
                "attempt": 2,
                "token_usage_json": '{"total": 7}',
                "input_json": {
                    "nodeOutputKeys": ["parent", "child", "missing", 3],
                    "prompt": "go",
                },
                "system_context_json": '{"role": "system"}',
                "duration_ms": 50,
                "started_at": 100,
                "completed_at": 1_700_000_000_000,
                "error_text": "boom",
                "session_id": "s1",
                "session_key": "key",
                "embedded_session_key": "embedded",
                "branch_id": "b1",
                "progress_message": "halfway",
                "triggered_by": "user",
                "output_json": '{"partial": true}',
            },
        ],
    }

    graph = mod._build_claw_mind_execution_graph(ext, run_status="running")

    assert graph is not None
    assert graph["run_id"] == 12
    assert graph["status"] == Status.RUNNING.value
    assert graph["output"] == {"answer": 42}
    assert graph["extend_props"]["params"] == {"topic": "coverage"}
    assert graph["extend_props"]["output"] == {"answer": 42}
    assert graph["relations"] == [
        {
            "src_id": "parent",
            "dst_id": "child",
            "type": "DEPENDENCY",
            "extend_props": {},
        }
    ]
    child = graph["tasks"][1]
    assert child["task_spec"]["context"]["title"] == "Child title"
    assert child["run_info"]["start_time"] == 100_000
    assert child["run_info"]["end_time"] == 1_700_000_000_000
    assert child["run_info"]["output"] == {"partial": True}
    assert child["run_info"]["extend_props"] == {
        "executor_type": "bot",
        "attempt": 2,
        "token_usage": {"total": 7},
        "input": {
            "nodeOutputKeys": ["parent", "child", "missing", 3],
            "prompt": "go",
        },
        "system_context": {"role": "system"},
        "duration_ms": 50,
        "started_at": 100,
        "completed_at": 1_700_000_000_000,
        "error_text": "boom",
        "session_id": "s1",
        "session_key": "key",
        "embedded_session_key": "embedded",
        "branch_id": "b1",
        "progress_message": "halfway",
        "triggered_by": "user",
    }


def test_claw_mind_builder_handles_empty_optional_values_and_invalid_run_id():
    graph = mod._build_claw_mind_execution_graph(
        {
            "flow_runs": {
                "id": "not-an-int",
                "params_json": "{}",
                "result_json": "{}",
            },
            "node_executions": [{"node_id": "n1", "input_json": {}}],
        },
        run_status="unknown",
    )
    assert graph is not None
    assert graph["run_id"] == 0
    assert graph["extend_props"] == {}
    assert graph["output"] == {}
    assert graph["tasks"][0]["status"] == Status.PENDING.value


def test_status_helpers_and_bcn_run_id_cover_every_mapping():
    assert mod._manager_worker_status("task.completed") == Status.DONE
    assert mod._manager_worker_status("session.created") == Status.RUNNING
    assert mod._bcn_node_status("success") == Status.DONE
    assert mod._bcn_node_status("error") == Status.FAILED
    assert mod._bcn_node_status("aborted") == Status.CANCELLED
    assert mod._bcn_node_status("waiting") == Status.RUNNING
    assert mod._bcn_run_id_as_int(None) == 0
    assert mod._bcn_run_id_as_int("7") == 7
    assert mod._bcn_run_id_as_int("run-7") == 0


def test_build_bcn_graph_skips_malformed_entries_and_supports_fallback_nodes():
    detailed = mod._build_bcn_execution_graph(
        event_type="state_machine.run.started",
        run_id=3,
        run_detail={
            "run": {"output": "not-a-dict"},
            "nodes": [None, {}, {"node_id": "n1", "status": "failed"}],
        },
        graph_detail={
            "nodes": [None, {}, {"node_id": "n1"}],
            "edges": [None, {}, {"source": "n1"}, {"source": "n1", "target": "n2"}],
        },
    )
    assert detailed is not None
    assert detailed["output"] == {}
    assert detailed["extend_props"] == {}
    assert detailed["tasks"][0]["status"] == Status.FAILED.value
    assert detailed["relations"] == [
        {"src_id": "n1", "dst_id": "n2", "type": "DEPENDENCY", "extend_props": {}}
    ]

    fallback = mod._build_bcn_execution_graph(
        event_type="state_machine.run.completed",
        run_id=None,
        run_detail={
            "run": None,
            "nodes": [None, {}, {"node_id": "only", "status": "cancelled"}],
        },
        graph_detail={"nodes": [], "edges": []},
    )
    assert fallback is not None
    assert fallback["run_id"] is None
    assert fallback["tasks"][0]["node_id"] == "only"
    assert fallback["tasks"][0]["status"] == Status.CANCELLED.value


def test_enrichers_ignore_non_dict_callback_data_and_use_status_fallbacks(monkeypatch):
    enricher = mod.CallbackDataEnricher(None)
    bcn = _callback()
    bcn.data = []
    claw = _callback()
    claw.data = []

    assert _run(enricher.enrich_bcn(bcn, {}, "run")) is None
    enricher.enrich_claw_mind(claw, {})

    cd = _callback()
    enricher.enrich_claw_mind(
        cd,
        {
            "status": "running",
            "ext_info": {"node_executions": ["bad", {"node_id": "n1"}]},
        },
    )
    assert cd.data["execution_graph"]["status"] == Status.RUNNING.value

    monkeypatch.setattr(mod, "_build_claw_mind_execution_graph", lambda *a, **k: None)
    untouched = _callback()
    enricher.enrich_claw_mind(untouched, {"ext_info": {"flow_runs": {"id": 1}}})
    assert "execution_graph" not in untouched.data


def test_enrich_bcn_uses_scope_session_and_handles_fetch_and_builder_failures(monkeypatch):
    enricher = mod.CallbackDataEnricher(None)

    async def _raise(_run_id):
        raise RuntimeError("fetch exploded")

    monkeypatch.setattr(enricher, "_fetch_run_and_graph", _raise)
    cd = _callback({"result": {}})
    raw = {
        "event_type": "state_machine.run.completed",
        "scope": {"session_id": "scope-session"},
        "data": {"output": {"ok": True}},
    }
    assert _run(enricher.enrich_bcn(cd, raw, "run-x")) is None
    assert cd.data["execution_graph"]["output"] == {"ok": True}

    async def _none(_run_id):
        return None, None

    monkeypatch.setattr(enricher, "_fetch_run_and_graph", _none)
    monkeypatch.setattr(mod, "_build_bcn_execution_graph", lambda **kwargs: None)
    no_graph = _callback({"result": {}})
    assert _run(enricher.enrich_bcn(no_graph, {}, "run-y")) is None
    assert "execution_graph" not in no_graph.data


def test_fetch_without_injected_client_uses_temporary_async_client(monkeypatch):
    seen = {}

    class _Client:
        def __init__(self, *, base_url, timeout):
            seen["config"] = (base_url, timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            seen["closed"] = True

    enricher = mod.CallbackDataEnricher(
        SimpleNamespace(base_url="http://bcs/"), timeout=2.5
    )

    async def _gets(client, run_id):
        seen["call"] = (client, run_id)
        return {"run": {}}, {"nodes": []}

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(enricher, "_gets", _gets)

    result = _run(enricher._fetch_run_and_graph("run-9"))
    assert result == ({"run": {}}, {"nodes": []})
    assert seen["config"] == ("http://bcs", 2.5)
    assert seen["call"][1] == "run-9"
    assert seen["closed"] is True


def test_gets_accepts_independent_run_and_graph_statuses():
    responses = iter(
        [
            httpx.Response(200, json={"run": {}}),
            httpx.Response(503, json={}),
        ]
    )

    class _Client:
        async def get(self, path):
            return next(responses)

    result = _run(mod.CallbackDataEnricher(None)._gets(_Client(), "run-1"))
    assert result == ({"run": {}}, None)


def test_enrich_bcn_prefers_callback_workflow_instance_id(monkeypatch):
    enricher = mod.CallbackDataEnricher(None)

    async def _none(_run_id):
        return None, None

    monkeypatch.setattr(enricher, "_fetch_run_and_graph", _none)
    cd = _callback({"workflow_instance_id": "callback-session", "result": {}})
    raw = {
        "event_type": "state_machine.run.started",
        "scope": {"session_id": "scope-session"},
        "data": {},
    }
    assert _run(enricher.enrich_bcn(cd, raw, "run-z")) is None
    assert cd.data["execution_graph"]["status"] == Status.RUNNING.value
