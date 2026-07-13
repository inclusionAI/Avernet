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
                                        '{"role":"assistant","content":"hello"}',
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
                }
            ],
        }
        first_relation = client.post(
            "/api/bot-chat/log-relations",
            json=relation_payload,
        )
        assert first_relation.status_code == 200, first_relation.text
        assert first_relation.json()["data"] == {
            "inserted": 1,
            "updated": 0,
            "total": 1,
        }

        second_relation = client.post(
            "/api/bot-chat/log-relations",
            json=relation_payload,
        )
        assert second_relation.status_code == 200, second_relation.text
        assert second_relation.json()["data"] == {
            "inserted": 0,
            "updated": 1,
            "total": 1,
        }
