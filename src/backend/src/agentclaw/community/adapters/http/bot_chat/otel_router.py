"""OTLP trace ingest endpoint for OCB-owned agent logs."""

import json
import logging
import os
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from agentclaw.community.core.repository.protocols.chat import BotChatDbRepositoryProtocol
from agentclaw.community.core.bot_chat.schemas import ApiResponse
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()
# SOFAPy derives backend-agent-logs.log/error/fatal files from this logger name.
agent_log_logger = get_logger("backend-agent-logs")

router = APIRouter(prefix="/api/bot-chat/otel", tags=["bot-chat"])


def _write_agent_log(level: int, message: str, **fields: Any) -> None:
    try:
        record = {"message": message, **fields}
        agent_log_logger.log(level, json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        logger.exception("Failed to write backend agent log")


def _write_otlp_request_log(
    *,
    payload: dict[str, Any],
    status: str,
    trace_count: int = 0,
    observation_count: int = 0,
    trace_ids: list[str] | None = None,
    error: str | None = None,
) -> None:
    _write_agent_log(
        logging.INFO if status == "success" else logging.WARNING,
        "otlp_traces_request",
        record_type="ocb_bot_chat_otel_traces",
        status=status,
        trace_count=trace_count,
        observation_count=observation_count,
        trace_ids=trace_ids or [],
        payload_digest=_payload_digest(payload),
        payload=payload,
        error=error,
    )


class OtlpIngestResult(BaseModel):
    trace_count: int = 0
    observation_count: int = 0


def _value_from_otlp(value: dict[str, Any]) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        raw = value["intValue"]
        if (
            isinstance(raw, str)
            and raw.isdigit()
        ):
            return int(raw)
        return raw
    if "doubleValue" in value:
        return value["doubleValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "arrayValue" in value:
        return [
            _value_from_otlp(item)
            for item in value["arrayValue"].get("values", [])
        ]
    if "kvlistValue" in value:
        return _attrs_to_dict(value["kvlistValue"].get("values", []))
    return None


def _attrs_to_dict(attrs: list[dict[str, Any]] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in attrs or []:
        key = item.get("key")
        value = item.get("value")
        if (
            key
            and isinstance(value, dict)
        ):
            result[key] = _value_from_otlp(value)
    return result


def _parse_json_attr(attrs: dict[str, Any], key: str) -> Any:
    value = attrs.get(key)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _nanos_to_ms(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value) // 1_000_000
    except (TypeError, ValueError):
        return None


def _payload_digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{sha256(data.encode('utf-8')).hexdigest()}"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_number(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _latency_ms(start_ms: int | None, end_ms: int | None) -> int | None:
    if (
        start_ms is None
        or end_ms is None
    ):
        return None
    return end_ms - start_ms


def _usage(attrs: dict[str, Any]) -> dict[str, int | None]:
    input_tokens = _int_number(attrs.get("gen_ai.usage.input_tokens"))
    output_tokens = _int_number(attrs.get("gen_ai.usage.output_tokens"))
    cache_read_tokens = _int_number(
        attrs.get("gen_ai.usage.cache_read.input_tokens")
        or attrs.get("agentic.usage.cache_read_tokens")
        or attrs.get("agentic.usage.raw.cache_read_input_tokens")
    )
    cache_write_tokens = _int_number(
        attrs.get("gen_ai.usage.cache_creation.input_tokens")
        or attrs.get("agentic.usage.cache_write_tokens")
        or attrs.get("agentic.usage.raw.cache_creation_input_tokens")
    )
    total_tokens = _int_number(attrs.get("gen_ai.usage.total_tokens"))
    if (
        total_tokens is None
        and input_tokens is not None
        and output_tokens is not None
    ):
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": total_tokens,
    }


def _model(attrs: dict[str, Any]) -> str | None:
    return attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")


def _usage_details(usage: dict[str, int | None], attrs: dict[str, Any]) -> dict[str, int]:
    raw_input = _int_number(
        attrs.get("agentic.usage.fresh_input_tokens")
        or attrs.get("agentic.usage.raw.input_tokens")
    )
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    cache_read_tokens = usage.get("cache_read_tokens")
    cache_write_tokens = usage.get("cache_write_tokens")

    details: dict[str, int] = {}
    if raw_input is not None:
        details["input"] = raw_input
    elif input_tokens is not None:
        cache_total = (cache_read_tokens or 0) + (cache_write_tokens or 0)
        details["input"] = max(input_tokens - cache_total, 0) if cache_total else input_tokens
    if output_tokens is not None:
        details["output"] = output_tokens
    if cache_read_tokens:
        details["cache_read_input_tokens"] = cache_read_tokens
    if cache_write_tokens:
        details["cache_creation_input_tokens"] = cache_write_tokens

    explicit_total = usage.get("total_tokens")
    if explicit_total is not None:
        details["total"] = explicit_total
    elif details:
        details["total"] = sum(details.values())
    return details


def _price_per_1m(env_key: str, default: float) -> float:
    return _number(os.environ.get(env_key)) or default


def _cost_details(usage_details: dict[str, int], attrs: dict[str, Any]) -> dict[str, float]:
    explicit = _parse_json_attr(attrs, "cost_details")
    if isinstance(explicit, dict):
        parsed = {
            str(key): float(value)
            for key, value in explicit.items()
            if _number(value) is not None
        }
        if "total" not in parsed:
            parsed["total"] = sum(
                value
                for key, value in parsed.items()
                if key != "total"
            )
        return parsed

    total = _number(attrs.get("gen_ai.usage.cost") or attrs.get("calculated_total_cost"))
    if not usage_details:
        return {"total": total} if total is not None else {}

    prices = {
        "input": _price_per_1m("OCB_AGENT_LOG_PRICE_INPUT_PER_1M", 0.06924226527885756),
        "output": _price_per_1m("OCB_AGENT_LOG_PRICE_OUTPUT_PER_1M", 0.09854721549636805),
        "cache_read_input_tokens": _price_per_1m("OCB_AGENT_LOG_PRICE_CACHE_READ_PER_1M", 0.006924226527885756),
        "cache_creation_input_tokens": _price_per_1m("OCB_AGENT_LOG_PRICE_CACHE_WRITE_PER_1M", 0.03462113263942878),
    }
    details = {
        key: tokens / 1_000_000 * prices[key]
        for key, tokens in usage_details.items()
        if (
            key in prices
            and tokens
        )
    }
    details["total"] = total if total is not None else sum(details.values())
    return details


def _source(attrs: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": attrs.get("agentic.runtime.name") or "openclaw",
        "collector": "observ-openclaw",
    }


def _biz_value(attrs: dict[str, Any], name: str) -> Any:
    return attrs.get(f"agentic.{name}") or attrs.get(f"identity.{name}")


def _span_to_observation(span: dict[str, Any], resource_attrs: dict[str, Any]) -> dict[str, Any]:
    attrs = {**resource_attrs, **_attrs_to_dict(span.get("attributes"))}
    ctx_trace_id = span.get("traceId")
    span_id = span.get("spanId")
    start_ms = _nanos_to_ms(span.get("startTimeUnixNano"))
    end_ms = _nanos_to_ms(span.get("endTimeUnixNano"))
    status = span.get("status") or {}
    kind = attrs.get("gen_ai.span.kind") or "SPAN"
    input_value = _parse_json_attr(attrs, "gen_ai.input.messages")
    output_value = _parse_json_attr(attrs, "gen_ai.output.messages")
    if kind == "TOOL":
        input_value = _parse_json_attr(attrs, "gen_ai.tool.call.arguments") or input_value
        output_value = _parse_json_attr(attrs, "gen_ai.tool.call.result") or output_value
    usage = _usage(attrs)
    usage_details = _usage_details(usage, attrs)
    cost_details = _cost_details(usage_details, attrs)
    model = _model(attrs)

    return {
        "observation_id": span_id,
        "trace_id": ctx_trace_id,
        "parent_observation_id": span.get("parentSpanId") or None,
        "biz_task_id": _biz_value(attrs, "biz_task_id"),
        "biz_scene": _biz_value(attrs, "biz_scene"),
        "session_id": attrs.get("gen_ai.session.id"),
        "session_key": attrs.get("gen_ai.conversation.id") or attrs.get("session_id"),
        "type": kind,
        "name": span.get("name"),
        "model": model,
        "input": input_value,
        "output": output_value,
        "metadata": {"attributes": attrs},
        "start_time_ms": start_ms,
        "end_time_ms": end_ms,
        "latency_ms": _latency_ms(start_ms, end_ms),
        "status": "ERROR" if status.get("code") == 2 else "SUCCESS",
        "status_message": status.get("message"),
        "usage": usage,
        "usage_details": usage_details,
        "cost_details": cost_details,
        "total_cost": cost_details.get("total"),
        "payload_digest": _payload_digest(span),
    }


def _trace_from_root(root: dict[str, Any], resource_attrs: dict[str, Any]) -> dict[str, Any]:
    attrs = {**resource_attrs, **_attrs_to_dict(root.get("attributes"))}
    start_ms = _nanos_to_ms(root.get("startTimeUnixNano"))
    end_ms = _nanos_to_ms(root.get("endTimeUnixNano"))
    bot_id = attrs.get("identity.bot_id")
    user_id = attrs.get("identity.owner_id") or attrs.get("user.id") or attrs.get("ant.username")
    usage = _usage(attrs)
    usage_details = _usage_details(usage, attrs)
    cost_details = _cost_details(usage_details, attrs)
    return {
        "trace_id": root.get("traceId"),
        "biz_task_id": _biz_value(attrs, "biz_task_id"),
        "biz_scene": _biz_value(attrs, "biz_scene"),
        "session_id": attrs.get("gen_ai.session.id"),
        "session_key": attrs.get("gen_ai.conversation.id") or attrs.get("session_id"),
        "user_id": user_id,
        "bot_id": bot_id,
        "engine": attrs.get("agentic.runtime.name") or "openclaw",
        "collector": "observ-openclaw",
        "name": root.get("name") or "openclaw.chat",
        "input": _parse_json_attr(attrs, "gen_ai.input.messages"),
        "output": _parse_json_attr(attrs, "gen_ai.output.messages"),
        "metadata": {"attributes": attrs},
        "start_time_ms": start_ms,
        "end_time_ms": end_ms,
        "latency_ms": _latency_ms(start_ms, end_ms),
        "total_cost": cost_details.get("total"),
        "usage": usage,
        "usage_details": usage_details,
        "cost_details": cost_details,
        "payload_digest": _payload_digest(root),
    }


def _iter_resource_spans(payload: dict[str, Any]):
    for resource_span in payload.get("resourceSpans", []):
        resource_attrs = _attrs_to_dict((resource_span.get("resource") or {}).get("attributes"))
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                yield span, resource_attrs


@router.post("/traces", response_model=ApiResponse[OtlpIngestResult])
async def ingest_otlp_traces(
    payload: dict[str, Any] = Body(...),
    repo: BotChatDbRepositoryProtocol = Injected(BotChatDbRepositoryProtocol),
):
    """Receive OTLP JSON traces and persist them into OCB log tables."""
    spans = list(_iter_resource_spans(payload))
    if not spans:
        _write_otlp_request_log(payload=payload, status="rejected", error="no_spans")
        raise HTTPException(status_code=400, detail="No OTLP spans found")

    trace_count = 0
    observation_count = 0
    roots: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    trace_ids: set[str] = set()

    for span, resource_attrs in spans:
        observation = _span_to_observation(span, resource_attrs)
        if (
            not observation.get("trace_id")
            or not observation.get("observation_id")
        ):
            continue
        trace_ids.add(str(observation.get("trace_id")))
        repo.upsert_ocb_observation(observation)
        observation_count += 1

        attrs = {**resource_attrs, **_attrs_to_dict(span.get("attributes"))}
        trace_id = span.get("traceId")
        if attrs.get("gen_ai.span.kind") == "CHAT":
            roots[trace_id] = (span, resource_attrs)

    for span, resource_attrs in roots.values():
        trace = _trace_from_root(span, resource_attrs)
        if trace.get("trace_id"):
            repo.upsert_ocb_trace(trace, _source((trace.get("metadata") or {}).get("attributes") or {}))
            trace_count += 1

    logger.info("OTLP traces ingested: traces=%s observations=%s", trace_count, observation_count)
    _write_otlp_request_log(
        payload=payload,
        status="success",
        trace_count=trace_count,
        observation_count=observation_count,
        trace_ids=sorted(trace_ids),
    )
    return ApiResponse(
        success=True,
        message="ok",
        error_code=200,
        data=OtlpIngestResult(trace_count=trace_count, observation_count=observation_count),
    )
