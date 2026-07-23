"""Live bot-chat OTLP ingest, query, and business-relation lifecycle."""
from __future__ import annotations

import time
import uuid

import httpx
import pytest


HEADERS = {"x-user-id": "e2e_user"}


def _attr(key: str, value: str | int) -> dict:
    if isinstance(value, int):
        encoded = {"intValue": str(value)}
    else:
        encoded = {"stringValue": value}
    return {"key": key, "value": encoded}


def _otlp_payload(trace_id: str, start_ns: int) -> dict:
    end_ns = start_ns + 1_000_000_000
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("identity.owner_id", "e2e_user"),
                        _attr("identity.bot_id", "default"),
                        _attr("agentic.runtime.name", "openclaw"),
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": "chat-root",
                                "name": "Singlebox chat turn",
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(end_ns),
                                "attributes": [
                                    _attr("gen_ai.span.kind", "CHAT"),
                                    _attr("gen_ai.session.id", "session-live"),
                                    _attr("gen_ai.conversation.id", "session-key-live"),
                                    _attr(
                                        "gen_ai.input.messages",
                                        '[{"role":"user","content":"hello singlebox"}]',
                                    ),
                                    _attr(
                                        "gen_ai.output.messages",
                                        '{"role":"assistant","content":"output-only-marker"}',
                                    ),
                                ],
                            },
                            {
                                "traceId": trace_id,
                                "spanId": "llm-child",
                                "parentSpanId": "chat-root",
                                "name": "Singlebox model call",
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(end_ns),
                                "attributes": [
                                    _attr("gen_ai.span.kind", "LLM"),
                                    _attr("gen_ai.response.model", "singlebox-model"),
                                    _attr("gen_ai.usage.input_tokens", 8),
                                    _attr("gen_ai.usage.output_tokens", 3),
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }


@pytest.mark.acceptance
def test_bot_chat_otel_ingest_query_and_relation_roundtrip(live_backend):
    trace_id = uuid.uuid4().hex
    payload = _otlp_payload(trace_id, time.time_ns())

    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=30.0) as client:
        ingest = client.post("/api/bot-chat/otel/traces", json=payload)
        assert ingest.status_code == 200, ingest.text
        ingest_body = ingest.json()
        assert ingest_body["success"] is True
        assert ingest_body["data"] == {
            "trace_count": 1,
            "observation_count": 2,
        }

        sessions = client.get(
            "/api/v1/bot-chats",
            params={"owner_id": "e2e_user", "trace_id": trace_id},
        )
        assert sessions.status_code == 200, sessions.text
        sessions_body = sessions.json()
        assert sessions_body["success"] is True
        assert sessions_body["data"]["total"] == 1
        assert sessions_body["data"]["sessions"][0]["id"] == trace_id

        detail = client.get(f"/api/v1/bot-chats/{trace_id}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()
        assert detail_body["success"] is True
        assert detail_body["data"]["id"] == trace_id
        assert detail_body["data"]["session_id"] == "session-live"
        assert detail_body["data"]["total_tokens"] == 11
        assert detail_body["data"]["observations"]

        relation_payload = {
            "biz_scene": "singlebox-coverage",
            "biz_task_id": trace_id,
            "engine": "openclaw",
            "collector": "observ-openclaw",
            "user_id": "e2e_user",
            "bot_id": "default",
            "refs": [
                {
                    "ref_type": "trace_id",
                    "ref_value": trace_id,
                },
                {
                    "ref_type": "session_id",
                    "ref_value": "session-live",
                },
                {
                    "ref_type": "session_key",
                    "ref_value": "session-key-live",
                },
            ],
        }
        first_relation = client.post(
            "/api/bot-chat/log-relations",
            json=relation_payload,
        )
        assert first_relation.status_code == 200, first_relation.text
        assert first_relation.json()["data"] == {
            "inserted": 3,
            "updated": 0,
            "total": 3,
        }

        second_relation = client.post(
            "/api/bot-chat/log-relations",
            json=relation_payload,
        )
        assert second_relation.status_code == 200, second_relation.text
        assert second_relation.json()["data"] == {
            "inserted": 0,
            "updated": 3,
            "total": 3,
        }

        task_queries = [
            {
                "biz_scene": "singlebox-coverage",
                "biz_task_id": trace_id,
            },
            {
                "biz_task_id": trace_id,
            },
            {
                "biz_scene": "singlebox-coverage",
            },
            {
                "biz_scene": "singlebox-cover",
                "biz_task_id": trace_id[:12],
                "match_mode": "contains",
            },
        ]
        for params in task_queries:
            task_result = client.get("/api/v1/bot-chats", params=params)
            assert task_result.status_code == 200, task_result.text
            task_body = task_result.json()
            assert task_body["success"] is True
            assert task_body["data"]["total"] == 1
            assert task_body["data"]["sessions"][0]["id"] == trace_id
            assert task_body["data"]["sessions"][0]["match_sources"]

        output_result = client.get(
            "/api/v1/bot-chats",
            params={
                "query": "output-only-marker",
                "include_output_match": "true",
            },
        )
        assert output_result.status_code == 200, output_result.text
        output_body = output_result.json()
        assert output_body["success"] is True
        assert output_body["data"]["total"] == 1
        assert output_body["data"]["sessions"][0]["id"] == trace_id

        identifier_queries = [
            {
                "session_id": "session-live",
                "match_mode": "exact",
            },
            {
                "session_key": "key-live",
                "match_mode": "contains",
            },
            {
                "bot_id": "default",
                "trace_id": trace_id,
            },
        ]
        for params in identifier_queries:
            identifier_result = client.get("/api/v1/bot-chats", params=params)
            assert identifier_result.status_code == 200, identifier_result.text
            identifier_body = identifier_result.json()
            assert identifier_body["success"] is True
            assert identifier_body["data"]["total"] == 1

        empty_group = client.get(
            "/api/v1/bot-chats",
            params={"group_id": "missing-group-fixture"},
        )
        assert empty_group.status_code == 200, empty_group.text
        assert empty_group.json()["data"]["total"] == 0

        historical_result = client.get(
            "/api/v1/bot-chats",
            params={
                "trace_id": trace_id,
                "time_scope": "all",
            },
        )
        assert historical_result.status_code == 200, historical_result.text
        assert historical_result.json()["data"]["total"] == 1

        invalid_unbounded = client.get(
            "/api/v1/bot-chats",
            params={"time_scope": "all"},
        )
        assert invalid_unbounded.status_code == 200, invalid_unbounded.text
        assert invalid_unbounded.json()["error_code"] == 4000

        invalid_fuzzy_range = client.get(
            "/api/v1/bot-chats",
            params={
                "query": "marker",
                "match_mode": "contains",
                "from_date": "2025-01-01T00:00:00",
                "to_date": "2025-04-02T00:00:01",
            },
        )
        assert invalid_fuzzy_range.status_code == 200, invalid_fuzzy_range.text
        assert invalid_fuzzy_range.json()["error_code"] == 4000

        isolated_relation = client.post(
            "/api/bot-chat/log-relations",
            json={
                "biz_scene": "isolated-scene",
                "biz_task_id": f"isolated-{trace_id}",
                "user_id": "different-user-fixture",
                "refs": [
                    {
                        "ref_type": "trace_id",
                        "ref_value": trace_id,
                    }
                ],
            },
        )
        assert isolated_relation.status_code == 200, isolated_relation.text
        isolated_query = client.get(
            "/api/v1/bot-chats",
            params={
                "biz_scene": "isolated-scene",
                "biz_task_id": f"isolated-{trace_id}",
            },
        )
        assert isolated_query.status_code == 200, isolated_query.text
        assert isolated_query.json()["data"]["total"] == 0
