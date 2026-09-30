import json
import logging

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.bot_chat import otel_router
from agentclaw.community.adapters.http.bot_chat.otel_router import (
    _span_to_observation,
    _trace_from_root,
    _value_from_otlp,
    _write_otlp_request_log,
    ingest_otlp_traces,
)


def _attr(key, value):
    if isinstance(value, int):
        otlp_value = {"intValue": str(value)}
    elif isinstance(value, float):
        otlp_value = {"doubleValue": value}
    else:
        otlp_value = {"stringValue": value}
    return {"key": key, "value": otlp_value}


def test_span_to_observation_derives_total_tokens_and_cost():
    observation = _span_to_observation(
        {
            "traceId": "trace-1",
            "spanId": "span-1",
            "name": "chat Kimi-K2.5",
            "startTimeUnixNano": "1782736696842000000",
            "endTimeUnixNano": "1782736706842000000",
            "attributes": [
                _attr("gen_ai.span.kind", "LLM"),
                _attr("agentic.biz_task_id", "task-1"),
                _attr("identity.biz_scene", "scene-a"),
                _attr("gen_ai.response.model", "Kimi-K2.5"),
                _attr("gen_ai.usage.input_tokens", 132487),
                _attr("gen_ai.usage.output_tokens", 826),
            ],
        },
        {},
    )

    assert observation["usage"]["total_tokens"] == 133313
    assert observation["biz_task_id"] == "task-1"
    assert observation["biz_scene"] == "scene-a"
    assert observation["usage_details"] == {
        "input": 132487,
        "output": 826,
        "total": 133313,
    }
    assert observation["cost_details"]["input"] == pytest.approx(0.0091737)
    assert observation["cost_details"]["output"] == pytest.approx(0.0000814)
    assert observation["total_cost"] == pytest.approx(0.0092551)


def test_trace_from_root_does_not_derive_zero_cost_without_usage():
    trace = _trace_from_root(
        {
            "traceId": "trace-1",
            "spanId": "chat-1",
            "name": "Turn 1",
            "startTimeUnixNano": "1782736696842000000",
            "endTimeUnixNano": "1782736706842000000",
            "attributes": [
                _attr("gen_ai.span.kind", "CHAT"),
                _attr("identity.biz_task_id", "task-root"),
                _attr("agentic.biz_scene", "scene-root"),
                _attr("identity.owner_id", "197444"),
                _attr("identity.bot_id", "default"),
            ],
        },
        {},
    )

    assert trace["usage_details"] == {}
    assert trace["biz_task_id"] == "task-root"
    assert trace["biz_scene"] == "scene-root"
    assert trace["cost_details"] == {}
    assert trace["total_cost"] is None


def test_value_from_otlp_handles_nested_values():
    value = {
        "kvlistValue": {
            "values": [
                _attr("items", "ignored") | {
                    "value": {
                        "arrayValue": {
                            "values": [
                                {"intValue": "1"},
                                {"boolValue": True},
                            ]
                        }
                    }
                },
                _attr("name", "openclaw"),
            ]
        }
    }

    assert _value_from_otlp(value) == {
        "items": [1, True],
        "name": "openclaw",
    }


def test_tool_span_parses_tool_payload_cache_breakdown_and_explicit_cost():
    observation = _span_to_observation(
        {
            "traceId": "trace-tool",
            "spanId": "tool-1",
            "name": "write",
            "startTimeUnixNano": "1782736696842000000",
            "endTimeUnixNano": "1782736699842000000",
            "status": {"code": 2, "message": "tool failed"},
            "attributes": [
                _attr("gen_ai.span.kind", "TOOL"),
                _attr("gen_ai.tool.call.arguments", '{"path":"a.txt"}'),
                _attr("gen_ai.tool.call.result", '{"ok":false}'),
                _attr("gen_ai.usage.input_tokens", 150),
                _attr("gen_ai.usage.output_tokens", 10),
                _attr("gen_ai.usage.cache_read.input_tokens", 100),
                _attr("gen_ai.usage.cache_creation.input_tokens", 20),
                _attr("cost_details", '{"input":"1.2","output":0.3}'),
            ],
        },
        {},
    )

    assert observation["input"] == {"path": "a.txt"}
    assert observation["output"] == {"ok": False}
    assert observation["status"] == "ERROR"
    assert observation["status_message"] == "tool failed"
    assert observation["latency_ms"] == 3000
    assert observation["usage"]["total_tokens"] == 160
    assert observation["usage_details"] == {
        "input": 30,
        "output": 10,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 20,
        "total": 160,
    }
    assert observation["cost_details"] == {
        "input": 1.2,
        "output": 0.3,
        "total": 1.5,
    }


def test_otlp_request_log_contains_full_payload(tmp_path, monkeypatch):
    log_file = tmp_path / "backend-agent-logs.log"
    original_handlers = list(otel_router.agent_log_logger.handlers)
    original_level = otel_router.agent_log_logger.level
    original_propagate = otel_router.agent_log_logger.propagate
    for handler in list(otel_router.agent_log_logger.handlers):
        otel_router.agent_log_logger.removeHandler(handler)

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    otel_router.agent_log_logger.addHandler(handler)
    otel_router.agent_log_logger.setLevel(logging.INFO)
    otel_router.agent_log_logger.propagate = False

    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "trace-log",
                                "spanId": "span-log",
                                "name": "Turn 1",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    try:
        _write_otlp_request_log(
            payload=payload,
            status="success",
            trace_count=1,
            observation_count=1,
            trace_ids=["trace-log"],
        )
    finally:
        otel_router.agent_log_logger.removeHandler(handler)
        handler.close()
        for original_handler in original_handlers:
            otel_router.agent_log_logger.addHandler(original_handler)
        otel_router.agent_log_logger.setLevel(original_level)
        otel_router.agent_log_logger.propagate = original_propagate

    line = log_file.read_text(encoding="utf-8").strip()
    record = json.loads(line.split("INFO ", 1)[1])
    assert record["message"] == "otlp_traces_request"
    assert record["payload"] == payload
    assert record["trace_ids"] == ["trace-log"]
    assert record["payload_digest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_ingest_otlp_traces_rejects_payload_without_spans(monkeypatch):
    logged = []
    monkeypatch.setattr(otel_router, "_write_otlp_request_log", lambda **kwargs: logged.append(kwargs))

    with pytest.raises(HTTPException) as exc_info:
        await ingest_otlp_traces(payload={"resourceSpans": []}, repo=object())

    assert exc_info.value.status_code == 400
    assert logged == [
        {
            "payload": {"resourceSpans": []},
            "status": "rejected",
            "error": "no_spans",
        }
    ]


@pytest.mark.asyncio
async def test_ingest_otlp_traces_persists_observations_and_chat_trace(monkeypatch):
    calls = {
        "observations": [],
        "traces": [],
        "logs": [],
    }

    class FakeRepo:
        def upsert_ocb_observation(self, observation):
            calls["observations"].append(observation)

        def upsert_ocb_trace(self, trace, source):
            calls["traces"].append((trace, source))

    monkeypatch.setattr(otel_router, "_write_otlp_request_log", lambda **kwargs: calls["logs"].append(kwargs))

    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("identity.owner_id", "197444"),
                        _attr("identity.bot_id", "default"),
                        _attr("agentic.runtime.name", "openclaw"),
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "trace-ingest",
                                "spanId": "llm-1",
                                "name": "LLM",
                                "attributes": [
                                    _attr("gen_ai.span.kind", "LLM"),
                                    _attr("gen_ai.usage.input_tokens", 1),
                                    _attr("gen_ai.usage.output_tokens", 2),
                                ],
                            },
                            {
                                "traceId": "trace-ingest",
                                "spanId": "chat-1",
                                "name": "Turn 1",
                                "attributes": [
                                    _attr("gen_ai.span.kind", "CHAT"),
                                    _attr("gen_ai.session.id", "session-real"),
                                    _attr("gen_ai.conversation.id", "session-key"),
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }

    result = await ingest_otlp_traces(payload=payload, repo=FakeRepo())

    assert result.success is True
    assert result.data.trace_count == 1
    assert result.data.observation_count == 2
    assert len(calls["observations"]) == 2
    trace, source = calls["traces"][0]
    assert trace["trace_id"] == "trace-ingest"
    assert trace["session_id"] == "session-real"
    assert trace["session_key"] == "session-key"
    assert source == {
        "engine": "openclaw",
        "collector": "observ-openclaw",
    }
    assert calls["logs"][0]["status"] == "success"
    assert calls["logs"][0]["trace_ids"] == ["trace-ingest"]


@pytest.mark.asyncio
async def test_ingest_otlp_traces_does_not_promote_child_span_to_trace(monkeypatch):
    calls = {
        "observations": [],
        "traces": [],
        "logs": [],
    }

    class FakeRepo:
        def upsert_ocb_observation(self, observation):
            calls["observations"].append(observation)

        def upsert_ocb_trace(self, trace, source):
            calls["traces"].append((trace, source))

    monkeypatch.setattr(otel_router, "_write_otlp_request_log", lambda **kwargs: calls["logs"].append(kwargs))

    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("identity.owner_id", "197444"),
                        _attr("identity.bot_id", "default"),
                        _attr("agentic.runtime.name", "openclaw"),
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "trace-child-only",
                                "spanId": "tool-1",
                                "parentSpanId": "chat-1",
                                "name": "tool exec",
                                "attributes": [
                                    _attr("gen_ai.span.kind", "TOOL"),
                                    _attr("gen_ai.conversation.id", "session-key"),
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }

    result = await ingest_otlp_traces(payload=payload, repo=FakeRepo())

    assert result.success is True
    assert result.data.trace_count == 0
    assert result.data.observation_count == 1
    assert calls["observations"][0]["name"] == "tool exec"
    assert calls["observations"][0]["type"] == "TOOL"
    assert calls["traces"] == []
    assert calls["logs"][0]["status"] == "success"
    assert calls["logs"][0]["trace_count"] == 0
    assert calls["logs"][0]["observation_count"] == 1
    assert calls["logs"][0]["trace_ids"] == ["trace-child-only"]
