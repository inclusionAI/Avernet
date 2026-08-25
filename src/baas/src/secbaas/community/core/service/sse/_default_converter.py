"""默认 StreamConverter 实现（BCN 协议）

由 SseConverterFactory 通过 DI 注入，按名称实例化。

转换逻辑:
  - chunk.type == "delta"     → SSE event: chat (增量文本)
  - chunk.type == "final"     → SSE event: chat (最终完整文本 + usage)
  - chunk.type == "error"     → SSE event: chat (错误信息)
  - chunk.type == "aborted"   → SSE event: chat (中止)
  - chunk.type == "agent"     → SSE event: agent (引擎事件)
  - chunk.type == "heartbeat" → SSE 注释帧: : heartbeat
  - chunk.type == "interaction" → SSE event: interaction (扁平 BCN 数据)
  - chunk.type == "usage"     → 无独立事件（忽略）
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any

from secbaas.community.api.sse import SseEvent, StreamChunk
from secbaas.community.logger import get_logger

logger = get_logger("bcn-converter")

_LOG_TEXT_PREVIEW_CHARS = 10
_INTERACTION_EVENT_PHASES = {
    "interaction.requested": "requested",
    "interaction.resolved": "resolved",
    "mode_transition.resolved": "resolved",
}
_INTERACTION_KINDS = {"ask_user", "exec", "mode_switch"}
_DEFAULT_EXEC_OPTIONS = (
    {"label": "Allow once", "decision": "allow-once"},
    {"label": "Allow always", "decision": "allow-always"},
    {"label": "Deny", "decision": "deny"},
)


def _safe_log_string(value: Any) -> str:
    with suppress(Exception):
        return str(value)
    with suppress(Exception):
        return repr(value)
    return "<unserializable>"


def _log_json(value: Any) -> str:
    with suppress(Exception):
        return json.dumps(
            value,
            ensure_ascii=False,
            default=_safe_log_string,
            separators=(",", ":"),
        )
    return _safe_log_string(value)


def _preview_log_text(value: Any) -> Any:
    if not isinstance(value, str) or len(value) <= _LOG_TEXT_PREVIEW_CHARS:
        return value
    return f"{value[:_LOG_TEXT_PREVIEW_CHARS]}…<truncated:{len(value)}>"


def _thinking_metadata_log_payload(metadata: Any) -> Any:
    if not isinstance(metadata, dict):
        return metadata
    engine_frame = metadata.get("engine_frame")
    if not isinstance(engine_frame, dict) or engine_frame.get("stream") != "thinking":
        return metadata
    data = engine_frame.get("data")
    if not isinstance(data, dict):
        return metadata

    projected_data = dict(data)
    for key in ("delta", "text"):
        if key in projected_data:
            projected_data[key] = _preview_log_text(projected_data[key])
    projected_frame = dict(engine_frame)
    projected_frame["data"] = projected_data
    projected_metadata = dict(metadata)
    projected_metadata["engine_frame"] = projected_frame
    return projected_metadata


def _chunk_log_payload(chunk: StreamChunk) -> dict[str, Any]:
    content = chunk.content
    metadata = chunk.metadata
    if chunk.type in {"delta", "final", "error", "aborted"}:
        content = _preview_log_text(content)
    elif chunk.type == "agent":
        metadata = _thinking_metadata_log_payload(metadata)
    return {
        "type": chunk.type,
        "content": content,
        "usage": chunk.usage,
        "metadata": metadata,
        "engine_type": chunk.engine_type,
    }


def _preview_chat_message(data: dict[str, Any]) -> None:
    message = data.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return

    projected_content = []
    for item in content:
        if isinstance(item, dict) and "text" in item:
            projected_item = dict(item)
            projected_item["text"] = _preview_log_text(projected_item["text"])
            projected_content.append(projected_item)
        else:
            projected_content.append(item)
    projected_message = dict(message)
    projected_message["content"] = projected_content
    data["message"] = projected_message


def _event_log_data(event: SseEvent) -> str:
    if event.event not in {"chat", "agent"}:
        return event.data
    data = json.loads(event.data)
    if not isinstance(data, dict):
        return event.data
    projected_data = dict(data)
    if event.event == "chat":
        for key in ("deltaText", "errorMessage"):
            if key in projected_data:
                projected_data[key] = _preview_log_text(projected_data[key])
        _preview_chat_message(projected_data)
    elif projected_data.get("stream") == "thinking":
        for key in ("delta", "text"):
            if key in projected_data:
                projected_data[key] = _preview_log_text(projected_data[key])
    return json.dumps(projected_data, ensure_ascii=False)


def _event_log_payload(event: SseEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "event": event.event,
        "data": _event_log_data(event),
        "id": event.id,
        "retry": event.retry,
    }


def _chunk_log_json(chunk: StreamChunk) -> str:
    with suppress(Exception):
        return _log_json(_chunk_log_payload(chunk))
    return "<unserializable>"


def _event_log_json(event: SseEvent | None) -> str:
    with suppress(Exception):
        return _log_json(_event_log_payload(event))
    return "<unserializable>"


class DefaultStreamConverter:
    """Convert Baas / engine stream chunks to the BCN downlink SSE protocol.

    Dispatch is driven by ``StreamChunk.type`` (delta / final / error / agent),
    not by an inner engine envelope:
    - ``delta``  -> chat delta, carrying only the incremental ``deltaText``
      (BCS self-accumulates by run_id; no ``message`` is sent).
    - ``final``  -> chat final terminal marker (no body; BCS flushes its
      accumulated buffer).
    - ``error``  -> chat error, carrying the error text.
    - ``agent``  -> agent event; ``metadata["engine_frame"]`` holds the raw
      engine payload (``stream`` + ``data``), handled as the payload directly.

    Every event's ``ts`` is stamped at conversion time (receipt order) rather
    than read from the engine frame, so downstream sequencing never reorders.
    """

    def __init__(self, *, conversion_logging_enabled: bool = False) -> None:
        self._seq = 0
        self._conversion_logging_enabled = conversion_logging_enabled

    @staticmethod
    def name() -> str:
        return "default"

    def convert(self, chunk: StreamChunk, *, run_id: str) -> SseEvent | None:
        raw_input = _chunk_log_json(chunk) if self._conversion_logging_enabled else ""
        try:
            converted = _transform_chunk(chunk, _engine_name(chunk), run_id)
            if converted is None:
                event = None
            elif converted["event"].startswith(":"):
                event = SseEvent(event=converted["event"], data="")
            else:
                event = self._build_event(converted["event"], converted["data"], run_id)
        except Exception as exc:
            if self._conversion_logging_enabled:
                with suppress(Exception):
                    logger.exception(
                        "[convert] source=bcn_downlink run_id=%s input=%s output=%s",
                        run_id,
                        raw_input,
                        _log_json(
                            {
                                "error_type": type(exc).__name__,
                                "error_message": _safe_log_string(exc),
                            }
                        ),
                    )
            raise
        if self._conversion_logging_enabled:
            with suppress(Exception):
                logger.info(
                    "[convert] source=bcn_downlink run_id=%s input=%s output=%s",
                    run_id,
                    raw_input,
                    _event_log_json(event),
                )
        return event

    def _build_event(
        self,
        event: str,
        data: dict[str, Any],
        run_id: str,
    ) -> SseEvent:
        self._seq += 1
        payload = dict(data)
        payload.setdefault("runId", run_id)
        payload["seq"] = self._seq
        # Stamp ts at receipt time (ms). chat frames no longer carry a payload,
        # and using the engine's ts risks out-of-order timestamps across the
        # chat/agent interleave. seq already fixes ordering; ts is informational.
        payload.setdefault("ts", int(time.time() * 1000))
        return SseEvent(
            event=event,
            id=str(self._seq),
            data=json.dumps(payload, ensure_ascii=False),
        )


def _engine_name(chunk: StreamChunk) -> str:
    # Prefer the strongly-typed engine_type (set by ClawBotService via
    # `replace(chunk, engine_type=...)`); fall back to metadata for producers
    # that only tag the engine there.
    if chunk.engine_type:
        return str(chunk.engine_type)
    metadata = chunk.metadata or {}
    value = (
        metadata.get("engine")
        or metadata.get("engine_type")
        or metadata.get("engineType")
        or metadata.get("provider")
        or ""
    )
    return str(value)


def _agent_payload(chunk: StreamChunk) -> dict[str, Any]:
    """The raw engine payload for an agent chunk lives under
    ``metadata["engine_frame"]`` (stored directly, NOT wrapped in a
    type/event/payload envelope)."""
    metadata = chunk.metadata or {}
    payload = metadata.get("engine_frame")
    return payload if isinstance(payload, dict) else {}


def _transform_chunk(
    chunk: StreamChunk,
    engine: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Route by StreamChunk.type. delta/final/error are chat events built from
    the chunk's own fields; agent events read the raw engine payload."""
    ctype = chunk.type

    if ctype == "delta":
        data: dict[str, Any] = {"state": "delta"}
        if chunk.content:
            data["deltaText"] = chunk.content
        return {"event": "chat", "data": data}

    if ctype == "final":
        # Rebuild `message` from the chunk's full text (content == the engine's
        # message.content[].text on the BaaS side). BCS's relay path reads
        # message.content[].text to decide relay + to route the final to the
        # group, so an empty final would be skipped. For persistence BCS is in
        # self-accumulate mode and flushes its own delta buffer, ignoring this
        # message (no duplication). Deltas still carry only deltaText.
        data: dict[str, Any] = {"state": "final"}
        if chunk.content:
            data["message"] = {
                "role": "assistant",
                "content": [{"type": "text", "text": chunk.content}],
                "timestamp": int(time.time() * 1000),
            }
        metadata = chunk.metadata or {}
        _copy_present(metadata, data, ("stopReason", "usage"))
        return {"event": "chat", "data": data}

    if ctype == "error":
        data = {"state": "error", "errorMessage": chunk.content or "Unknown error"}
        metadata = chunk.metadata or {}
        _copy_present(metadata, data, ("errorKind",))
        return {"event": "chat", "data": data}

    if ctype == "aborted":
        data = {"state": "aborted"}
        metadata = chunk.metadata or {}
        _copy_present(metadata, data, ("stopReason",))
        return {"event": "chat", "data": data}

    if ctype == "agent":
        return _transform_agent(_agent_payload(chunk), engine)

    if ctype == "interaction":
        return _transform_interaction(chunk, run_id)

    if ctype == "heartbeat":
        return {"event": ": heartbeat", "data": {}}

    # usage / unknown: no standalone SSE event.
    return None


def _transform_interaction(
    chunk: StreamChunk,
    run_id: str,
) -> dict[str, Any] | None:
    metadata = chunk.metadata or {}
    event = metadata.get("event")
    if event not in _INTERACTION_EVENT_PHASES:
        _warn_interaction(
            run_id=run_id,
            field_path="metadata.event",
            error_type="unsupported_event",
        )
        return None

    envelope = metadata.get("payload")
    if not isinstance(envelope, dict):
        _warn_interaction(
            run_id=run_id,
            field_path="metadata.payload",
            error_type="invalid_envelope",
        )
        return None
    if envelope.get("type") != "event" or envelope.get("event") != event:
        _warn_interaction(
            run_id=run_id,
            field_path="metadata.payload",
            error_type="invalid_envelope",
        )
        return None

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        _warn_interaction(
            run_id=run_id,
            field_path="metadata.payload.payload",
            error_type="invalid_envelope",
        )
        return None

    phase = _INTERACTION_EVENT_PHASES[event]
    data = _common_interaction_data(payload, phase=phase, run_id=run_id)
    if data is None:
        return None

    if phase == "resolved":
        _copy_present(
            payload,
            data,
            ("decision", "action", "answers", "idempotencyKey"),
        )
        return {"event": "interaction", "data": data}

    try:
        if data["kind"] == "ask_user":
            converted = _transform_ask_user_requested(
                payload,
                data,
                run_id=run_id,
            )
        elif data["kind"] == "exec":
            converted = _transform_exec_requested(
                payload,
                data,
                run_id=run_id,
            )
        elif data["kind"] == "mode_switch":
            converted = _transform_mode_switch_requested(
                payload,
                data,
                run_id=run_id,
            )
        else:
            # Kind-specific requested mappings are added independently. Dropping an
            # unsupported requested shape is safer than leaking the Engine payload.
            return None
        if not converted:
            return None
        return {"event": "interaction", "data": data}
    except Exception as exc:
        _warn_interaction(
            run_id=run_id,
            interaction_id=data["interactionId"],
            kind=data["kind"],
            field_path="kind_converter",
            error_type=type(exc).__name__,
        )
        return None


def _transform_ask_user_requested(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    run_id: str,
) -> bool:
    raw_questions = payload.get("questions")
    questions = _convert_ask_user_questions(
        raw_questions,
        run_id=run_id,
        interaction_id=data["interactionId"],
        kind=data["kind"],
    )
    if not questions:
        _warn_interaction(
            run_id=run_id,
            interaction_id=data["interactionId"],
            kind=data["kind"],
            field_path="payload.questions",
            error_type="no_valid_questions",
        )
        return False
    data["questions"] = questions
    return True


def _convert_ask_user_questions(
    value: Any,
    *,
    run_id: str,
    interaction_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="payload.questions",
            error_type="invalid_type",
        )
        return []

    converted: list[dict[str, Any]] = []
    seen_headers: set[str] = set()
    for index, question in enumerate(value):
        field_path = f"payload.questions[{index}]"
        if not isinstance(question, dict):
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="invalid_type",
            )
            continue

        question_text = _non_empty_str(question.get("question"))
        if question_text is None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.question",
                error_type="missing_required_field",
            )
            continue

        header = _non_empty_str(question.get("header"))
        if header is None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.header",
                error_type="invalid_header",
            )
            return []

        converted_question: dict[str, Any] = {
            "questionId": f"question_{index + 1}",
            "question": question_text,
            "header": header,
        }
        has_options = "options" in question
        if has_options:
            options = _convert_ask_user_options(
                question.get("options"),
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.options",
            )
            if not options:
                continue
            converted_question["options"] = options

        _copy_optional_bool(
            question,
            converted_question,
            "multiSelect",
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path=f"{field_path}.multiSelect",
        )
        if has_options:
            _copy_optional_bool(
                question,
                converted_question,
                "allowOther",
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.allowOther",
            )
        elif question.get("allowOther") is not None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.allowOther",
                error_type="unsupported_without_options",
            )

        if header in seen_headers:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.header",
                error_type="duplicate_header",
            )
        if len(converted) >= 4:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="max_items_exceeded",
            )
            continue
        seen_headers.add(header)
        converted.append(converted_question)
    return converted


def _convert_ask_user_options(
    value: Any,
    *,
    run_id: str,
    interaction_id: str,
    kind: str,
    field_path: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path=field_path,
            error_type="invalid_type",
        )
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path=field_path,
            error_type="no_valid_options",
        )
        return []

    converted: list[dict[str, Any]] = []
    option_positions: dict[str, int] = {}
    for index, option in enumerate(value):
        option_path = f"{field_path}[{index}]"
        if not isinstance(option, dict):
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=option_path,
                error_type="invalid_type",
            )
            continue

        label = _non_empty_str(option.get("label"))
        option_value = _non_empty_str(option.get("decision")) or _non_empty_str(
            option.get("value")
        )
        if label is None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=option_path,
                error_type="missing_required_field",
            )
            continue
        if option_value is None:
            option_value = label
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=option_path,
                error_type="legacy_label_fallback",
            )

        converted_option: dict[str, Any] = {
            "label": label,
            "value": option_value,
        }
        _copy_present(option, converted_option, ("description",))
        existing_position = option_positions.get(option_value)
        if existing_position is not None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{option_path}.value",
                error_type="duplicate_option_value",
            )
            converted[existing_position] = converted_option
            continue
        if len(converted) >= 4:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=option_path,
                error_type="max_items_exceeded",
            )
            continue
        option_positions[option_value] = len(converted)
        converted.append(converted_option)
    if not converted:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path=field_path,
            error_type="no_valid_options",
        )
    return converted


def _copy_optional_bool(
    source: dict[str, Any],
    target: dict[str, Any],
    key: str,
    *,
    run_id: str,
    interaction_id: str,
    kind: str,
    field_path: str,
) -> None:
    value = source.get(key)
    if value is None:
        return
    if isinstance(value, bool):
        target[key] = value
        return
    _warn_interaction(
        run_id=run_id,
        interaction_id=interaction_id,
        kind=kind,
        field_path=field_path,
        error_type="invalid_type",
    )


def _transform_exec_requested(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    run_id: str,
) -> bool:
    command = _non_empty_str(payload.get("command"))
    if command is not None:
        data["command"] = command
    _copy_present(payload, data, ("cwd",))

    if "options" not in payload:
        data["options"] = [dict(option) for option in _DEFAULT_EXEC_OPTIONS]
        return True

    options = _convert_decision_options(
        payload.get("options"),
        run_id=run_id,
        interaction_id=data["interactionId"],
        kind=data["kind"],
    )
    if not options:
        _warn_interaction(
            run_id=run_id,
            interaction_id=data["interactionId"],
            kind=data["kind"],
            field_path="payload.options",
            error_type="no_valid_options",
        )
        return False
    data["options"] = options
    return True


def _transform_mode_switch_requested(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    run_id: str,
) -> bool:
    subject = payload.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    _copy_first_present(
        data,
        "fromMode",
        _non_empty_str(payload.get("fromMode")),
        _non_empty_str(subject.get("fromMode")),
    )
    _copy_first_present(
        data,
        "targetMode",
        _non_empty_str(payload.get("toMode")),
        _non_empty_str(subject.get("toMode")),
    )

    options = _convert_mode_switch_options(
        payload.get("options"),
        run_id=run_id,
        interaction_id=data["interactionId"],
        kind=data["kind"],
    )
    if not options:
        _warn_interaction(
            run_id=run_id,
            interaction_id=data["interactionId"],
            kind=data["kind"],
            field_path="payload.options",
            error_type="no_valid_options",
        )
        return False
    data["options"] = options
    return True


def _common_interaction_data(
    payload: dict[str, Any],
    *,
    phase: str,
    run_id: str,
) -> dict[str, Any] | None:
    interaction_id = _non_empty_str(payload.get("interactionId")) or _non_empty_str(
        payload.get("id")
    )
    kind = _non_empty_str(payload.get("kind"))
    if interaction_id is None:
        _warn_interaction(
            run_id=run_id,
            kind=kind or "",
            field_path="interactionId",
            error_type="missing_required_field",
        )
        return None
    if kind is None:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            field_path="kind",
            error_type="missing_required_field",
        )
        return None
    if kind not in _INTERACTION_KINDS:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="payload.kind",
            error_type="unsupported_kind",
        )
        return None

    payload_phase = payload.get("phase")
    if payload_phase is not None and payload_phase != phase:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="phase",
            error_type="phase_conflict",
        )

    data: dict[str, Any] = {
        "interactionId": interaction_id,
        "kind": kind,
        "phase": phase,
    }
    _copy_first_present(data, "title", payload.get("title"))
    _copy_first_present(data, "description", payload.get("description"))

    subject = payload.get("subject")
    subject_tool_call_id = (
        subject.get("toolCallId") if isinstance(subject, dict) else None
    )
    _copy_first_present(
        data,
        "toolCallId",
        _non_empty_str(payload.get("toolCallId")),
        _non_empty_str(subject_tool_call_id),
    )
    return data


def _convert_decision_options(
    value: Any,
    *,
    run_id: str,
    interaction_id: str,
    kind: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="payload.options",
            error_type="invalid_type",
        )
        return []

    converted: list[dict[str, str]] = []
    decision_positions: dict[str, int] = {}
    for index, option in enumerate(value):
        field_path = f"payload.options[{index}]"
        if not isinstance(option, dict):
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="invalid_type",
            )
            continue

        label = _non_empty_str(option.get("label"))
        decision = _non_empty_str(option.get("decision")) or _non_empty_str(
            option.get("value")
        )
        if label is None or decision is None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="missing_required_field",
            )
            continue
        converted_option = {"label": label, "decision": decision}
        existing_position = decision_positions.get(decision)
        if existing_position is not None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.decision",
                error_type="duplicate_option_decision",
            )
            converted[existing_position] = converted_option
            continue
        decision_positions[decision] = len(converted)
        converted.append(converted_option)
    return converted


def _convert_mode_switch_options(
    value: Any,
    *,
    run_id: str,
    interaction_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="payload.options",
            error_type="invalid_type",
        )
        return []

    converted: list[dict[str, Any]] = []
    decision_positions: dict[str, int] = {}
    for index, option in enumerate(value):
        field_path = f"payload.options[{index}]"
        if not isinstance(option, dict):
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="invalid_type",
            )
            continue

        label = _non_empty_str(option.get("label"))
        decision = _non_empty_str(option.get("decision")) or _non_empty_str(
            option.get("value")
        )
        if label is None or decision is None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="missing_required_field",
            )
            continue

        converted_option: dict[str, Any] = {
            "label": label,
            "decision": decision,
        }
        if "targetMode" in option:
            target_mode = _non_empty_str(option.get("targetMode"))
            if target_mode is None:
                _warn_interaction(
                    run_id=run_id,
                    interaction_id=interaction_id,
                    kind=kind,
                    field_path=f"{field_path}.targetMode",
                    error_type="invalid_type",
                )
            else:
                converted_option["targetMode"] = target_mode
        _copy_optional_bool(
            option,
            converted_option,
            "recommended",
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path=f"{field_path}.recommended",
        )

        existing_position = decision_positions.get(decision)
        if existing_position is not None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=f"{field_path}.decision",
                error_type="duplicate_option_decision",
            )
            converted[existing_position] = converted_option
            continue
        decision_positions[decision] = len(converted)
        converted.append(converted_option)
    return converted


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _copy_first_present(
    target: dict[str, Any],
    key: str,
    *values: Any,
) -> None:
    for value in values:
        if value is not None:
            target[key] = value
            return


def _warn_interaction(
    *,
    run_id: str,
    interaction_id: str = "",
    kind: str = "",
    field_path: str,
    error_type: str,
) -> None:
    logger.warning(
        "Interaction conversion warning: run_id=%s interaction_id=%s "
        "kind=%s field_path=%s error_type=%s",
        run_id,
        interaction_id,
        kind,
        field_path,
        error_type,
    )


def _transform_agent(payload: dict[str, Any], engine: str) -> dict[str, Any] | None:
    """Build an agent SSE event from the raw engine payload (stream + data)."""
    stream = payload.get("stream")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if stream == "thinking":
        out: dict[str, Any] = {"stream": "thinking"}
        _copy_present(data, out, ("delta", "text"))
        return {"event": "agent", "data": out}

    if stream == "tool":
        return _transform_tool(data, engine)

    if stream == "command_output":
        # Claude-format engines rely on command_output for the tool result.
        # Other engines (e.g. openclaw) ALSO emit a `tool`/`phase:result` frame
        # for the same toolCallId, so consuming command_output there would
        # persist a duplicate, name-less tool_result. Ignore it for other
        # engines.
        if not _is_claude_engine(engine):
            return None
        return _transform_command_output(data)

    if stream == "lifecycle":
        phase = data.get("phase")
        if phase not in {"start", "end"}:
            return None
        out = {"stream": "lifecycle", "phase": phase}
        _copy_present(data, out, ("model", "agentMode"))
        return {"event": "agent", "data": out}

    return None


def _transform_command_output(data: dict[str, Any]) -> dict[str, Any] | None:
    if data.get("phase") != "end":
        return None
    out: dict[str, Any] = {"stream": "tool", "phase": "result"}
    _copy_present(data, out, ("toolCallId",))
    output = data.get("output")
    if output is not None:
        out["result"] = _claude_tool_result(output)
    exit_code = data.get("exitCode")
    out["isError"] = isinstance(exit_code, int) and exit_code != 0
    _copy_present(data, out, ("exitCode", "durationMs", "cwd"))
    return {"event": "agent", "data": out}


def _transform_tool(data: dict[str, Any], engine: str) -> dict[str, Any] | None:
    if _is_claude_tool(data, engine):
        return _transform_claude_tool(data)
    return _transform_openclaw_tool(data)


def _is_claude_engine(engine: str) -> bool:
    return engine.replace("-", "_").lower() in {
        "aicoding",
        "claude",
        "claude_code",
    }


def _is_claude_tool(data: dict[str, Any], engine: str) -> bool:
    if _is_claude_engine(engine):
        return True
    return data.get("type") in {"start", "update", "result"} and any(
        key in data for key in ("toolName", "input", "output")
    )


def _transform_claude_tool(data: dict[str, Any]) -> dict[str, Any] | None:
    phase = {"start": "start", "result": "result"}.get(data.get("type"))
    if phase is None:
        return None

    out: dict[str, Any] = {"stream": "tool", "phase": phase}
    if data.get("toolName"):
        out["name"] = data["toolName"]
    if data.get("toolCallId"):
        out["toolCallId"] = data["toolCallId"]
    if phase == "start" and data.get("input") is not None:
        out["args"] = data["input"]
    if phase == "result":
        output = data.get("output")
        if output is not None:
            out["result"] = _claude_tool_result(output)
        out["isError"] = bool(data.get("isError", False))
    return {"event": "agent", "data": out}


def _claude_tool_result(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return output
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _transform_openclaw_tool(data: dict[str, Any]) -> dict[str, Any] | None:
    phase = data.get("phase")
    if phase not in {"start", "update", "result"}:
        return None

    out: dict[str, Any] = {"stream": "tool", "phase": phase}
    _copy_present(
        data,
        out,
        (
            "name",
            "toolCallId",
            "args",
            "result",
            "partialResult",
            "isError",
            "exitCode",
            "durationMs",
            "cwd",
        ),
    )
    return {"event": "agent", "data": out}


def _copy_present(
    source: dict[str, Any],
    target: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        if source.get(key) is not None:
            target[key] = source[key]
