"""Endpoint coverage for OCB bot-chat OTEL trace ingest."""
from __future__ import annotations

from agentclaw.community.core.bot_chat.models import AcOtelLogObservation, AcOtelLogTrace
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_PATH = "/api/bot-chat/otel/traces"


def _attr(key, value):
    if isinstance(value, int):
        otlp_value = {"intValue": str(value)}
    else:
        otlp_value = {"stringValue": value}
    return {"key": key, "value": otlp_value}


_HAPPY_BODY = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    _attr("identity.owner_id", "197444"),
                    _attr("identity.bot_id", "default"),
                ]
            },
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "trace-endpoint-otel",
                            "spanId": "llm-endpoint",
                            "name": "LLM",
                            "attributes": [
                                _attr("gen_ai.span.kind", "LLM"),
                                _attr("gen_ai.usage.input_tokens", 3),
                                _attr("gen_ai.usage.output_tokens", 4),
                            ],
                        },
                        {
                            "traceId": "trace-endpoint-otel",
                            "spanId": "chat-endpoint",
                            "name": "Turn 1",
                            "attributes": [
                                _attr("gen_ai.span.kind", "CHAT"),
                                _attr("gen_ai.session.id", "session-endpoint-real"),
                                _attr("gen_ai.conversation.id", "session-endpoint-key"),
                            ],
                        },
                    ]
                }
            ],
        }
    ]
}


def _assert_persisted(response, world) -> None:
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        trace = (
            session.query(AcOtelLogTrace)
            .filter(AcOtelLogTrace.trace_id == "trace-endpoint-otel")
            .one()
        )
        observations = (
            session.query(AcOtelLogObservation)
            .filter(AcOtelLogObservation.trace_id == "trace-endpoint-otel")
            .all()
        )
        trace_values = {
            "session_id": trace.session_id,
            "session_key": trace.session_key,
            "usage_input_tokens": trace.usage_input_tokens,
            "usage_output_tokens": trace.usage_output_tokens,
            "usage_total_tokens": trace.usage_total_tokens,
            "observation_count": len(observations),
        }

    assert trace_values["session_id"] == "session-endpoint-real"
    assert trace_values["session_key"] == "session-endpoint-key"
    assert trace_values["usage_input_tokens"] == 3
    assert trace_values["usage_output_tokens"] == 4
    assert trace_values["usage_total_tokens"] == 7
    assert trace_values["observation_count"] == 2


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="happy",
    input=CaseInput(json_body=_HAPPY_BODY),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "trace_count": 1,
                "observation_count": 2,
            },
        },
    ),
    extra_assertions=(_assert_persisted,),
)
def bot_chat_otel_ingest_happy():
    """Declarative case; the framework owns invocation."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="no_spans",
    input=CaseInput(json_body={"resourceSpans": []}),
    expect=ExpectError(
        status=400,
        json_contains={"detail": "No OTLP spans found"},
    ),
)
def bot_chat_otel_ingest_no_spans():
    """Declarative case; the framework owns invocation."""
