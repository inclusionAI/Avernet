from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import SessionRow
from ..utils import clean_query


def load_session_payload(row: SessionRow) -> dict[str, Any]:
    """Load one complete local trajectory and adapt it to OCSA's input schema.

    Diagnose owns only file discovery and wire-format adaptation.  It deliberately
    avoids head/tail truncation so OCSA receives the complete evidence required
    for task splitting, completion judgement, and tool assessment.
    """

    path = Path(row.path)
    if not path.exists():
        return {}
    objects: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = _unwrap(json.loads(line))
                except (TypeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                if isinstance(obj.get("messages"), (list, str)):
                    sid = str(obj.get("session_id") or obj.get("sessionId") or "")
                    if not row.session_id or not sid or sid == row.session_id:
                        return build_judge_session_payload([obj], row)
                objects.append(obj)
    except OSError:
        return {}
    return build_judge_session_payload(objects, row) if objects else {}


def _artifact_tool_names(objects: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for obj in objects:
        if _event_type(obj).lower() != "trace.artifacts":
            continue
        data = _event_data(obj)
        artifacts = data.get("artifacts") or obj.get("artifacts") or data
        if not isinstance(artifacts, dict):
            continue
        metas = artifacts.get("toolMetas") or artifacts.get("tool_metas") or []
        if isinstance(metas, dict):
            metas = list(metas.values())
        if not isinstance(metas, list):
            continue
        for meta in metas:
            if isinstance(meta, str):
                candidate = meta
            elif isinstance(meta, dict):
                candidate = (
                    meta.get("toolName")
                    or meta.get("tool_name")
                    or meta.get("name")
                    or meta.get("skillName")
                    or meta.get("skill_name")
                    or meta.get("mcpName")
                    or meta.get("mcp_name")
                    or ""
                )
            else:
                candidate = ""
            name = str(candidate).strip()
            if name and name not in names:
                names.append(name)
    return names


def _artifact_tool_messages(objects: list[dict[str, Any]], start_idx: int) -> list[dict[str, Any]]:
    """Expose observed tool calls without claiming that their result was successful."""
    return [
        {
            "idx": start_idx + offset,
            "role": "toolResult",
            "toolName": name,
            "content": "Observed tool invocation metadata; execution result unavailable.",
            "metadataOnly": True,
        }
        for offset, name in enumerate(_artifact_tool_names(objects))
    ]


def _artifact_signal(objects: list[dict[str, Any]], *keys: str) -> Any:
    """Return the first non-empty signal found in a trace artifact."""
    for obj in objects:
        if _event_type(obj).lower() != "trace.artifacts":
            continue
        data = _event_data(obj)
        containers = (data.get("artifacts"), data, obj.get("artifacts"), obj)
        for container in containers:
            if not isinstance(container, dict):
                continue
            for key in keys:
                value = container.get(key)
                if value not in (None, "", [], {}):
                    return value
    return None


def build_judge_session_payload(
    objects: list[dict[str, Any]], row: SessionRow | None = None
) -> dict[str, Any]:
    """Adapt raw OpenClaw events to the native OCSA session payload.

    A complete ``messagesSnapshot`` is the preferred source of truth.  Event-level
    messages are merged only when they add evidence absent from that snapshot.
    Trace artifacts remain supplemental signals and never override message roles.
    """

    if len(objects) == 1 and isinstance(objects[0].get("messages"), (list, str)):
        payload = dict(objects[0])
    else:
        messages = _merge_trajectory_messages(objects)
        observed_names = {
            name for message in messages for name in _message_tool_names(message)
        }
        metadata_messages = [
            message
            for message in _artifact_tool_messages(objects, len(messages))
            if str(message.get("toolName") or "") not in observed_names
        ]
        messages.extend(metadata_messages)
        payload = {
            "session_id": row.session_id if row else _first_value(
                objects, "session_id", "sessionId", "conversationId", "id"
            ),
            "botId": row.bot_id if row else _first_value(
                objects, "bot_id", "botId", "bot_uuid"
            ),
            "start_time": row.created_at if row else _first_value(
                objects, "created_at", "timestamp", "start_time"
            ),
            "messages": messages,
        }
        artifact_tool_use_stats = _artifact_signal(
            objects, "toolUseStats", "tool_use_stats"
        )
        artifact_stop_reason_stats = _artifact_signal(
            objects, "stopReasonStats", "stop_reason_stats"
        )
        if artifact_tool_use_stats is not None:
            payload["toolUseStats"] = artifact_tool_use_stats
        if artifact_stop_reason_stats is not None:
            payload["stopReasonStats"] = artifact_stop_reason_stats
    _fill_judge_signal_defaults(payload)
    return payload


def _merge_trajectory_messages(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshot = _latest_messages_snapshot(objects)
    messages = [
        normalized
        for idx, message in enumerate(snapshot)
        if (normalized := _normalise_message(message, idx))
    ]
    existing = {_message_signature(message) for message in messages}
    for obj in objects:
        normalized = _normalise_message(obj, len(messages))
        if not normalized:
            continue
        signature = _message_signature(normalized)
        if signature in existing:
            continue
        normalized["idx"] = len(messages)
        messages.append(normalized)
        existing.add(signature)
    return messages


def _latest_messages_snapshot(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[tuple[int, int, list[dict[str, Any]]]] = []
    for object_index, obj in enumerate(objects):
        for container in _event_containers(obj):
            value = container.get("messagesSnapshot") or container.get("messages_snapshot")
            if isinstance(value, str):
                value = _safe_json_parse(value)
            if not isinstance(value, list):
                continue
            messages = [item for item in value if isinstance(item, dict)]
            if messages:
                candidates.append((object_index, len(messages), messages))
    if not candidates:
        return []
    # Prefer the latest snapshot; message count breaks ties for envelope variants.
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _event_containers(obj: dict[str, Any]) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = [obj]
    data = obj.get("data")
    if isinstance(data, dict):
        containers.append(data)
        artifacts = data.get("artifacts")
        if isinstance(artifacts, dict):
            containers.append(artifacts)
    artifacts = obj.get("artifacts")
    if isinstance(artifacts, dict):
        containers.append(artifacts)
    return containers


def _message_signature(message: dict[str, Any]) -> str:
    comparable = {
        "role": message.get("role"),
        "toolName": message.get("toolName") or message.get("tool_name"),
        "content": _signature_content(message.get("content")),
        "isError": bool(message.get("isError")),
    }
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, default=str)


def _signature_content(value: Any) -> Any:
    if isinstance(value, list) and all(
        item is None or isinstance(item, (str, int, float, bool)) for item in value
    ):
        return "\n".join(str(item) for item in value if item is not None)
    return value


_TRAJECTORY_METADATA_TYPES = {
    "session.started",
    "trace.metadata",
    "session",
    "model_change",
    "thinking_level_change",
}


def _event_type(obj: dict[str, Any]) -> str:
    """Return the event type across the supported OpenClaw envelope shapes."""
    direct = obj.get("type") or obj.get("event") or obj.get("name")
    if direct:
        return str(direct).strip()
    data = obj.get("data")
    if isinstance(data, dict):
        nested = data.get("type") or data.get("event") or data.get("name")
        if nested:
            return str(nested).strip()
    return ""


def _event_data(obj: dict[str, Any]) -> dict[str, Any]:
    data = obj.get("data")
    return data if isinstance(data, dict) else {}


def _extract_final_prompt_text(obj: dict[str, Any]) -> str:
    data = _event_data(obj)
    candidates = [
        data.get("finalPromptText"),
        data.get("final_prompt_text"),
        obj.get("finalPromptText"),
        obj.get("final_prompt_text"),
    ]
    artifacts = data.get("artifacts") or obj.get("artifacts")
    if isinstance(artifacts, dict):
        candidates.extend(
            [artifacts.get("finalPromptText"), artifacts.get("final_prompt_text")]
        )
    for value in candidates:
        text = clean_query(_text_value(value))
        if _looks_like_message_text(text):
            return text
    return ""


def _is_trajectory_metadata(obj: dict[str, Any]) -> bool:
    event_type = _event_type(obj).lower()
    if event_type in _TRAJECTORY_METADATA_TYPES:
        return True
    if event_type == "trace.artifacts":
        return True
    return False


def _event_role(obj: dict[str, Any]) -> str:
    """Classify an event without treating prompt metadata as its event role."""
    event_type = _event_type(obj).lower()
    if event_type in {
        "model.completed",
        "model_completion",
        "message.completed",
        "message.assistant",
    }:
        return "assistant"
    if event_type == "trace.artifacts":
        return "metadata"
    if _extract_final_prompt_text(obj):
        return "user"
    role = str(
        obj.get("role") or obj.get("type") or obj.get("speaker") or ""
    ).strip().lower()
    data = _event_data(obj)
    if role in {
        "user",
        "human",
        "assistant",
        "agent",
        "bot",
        "tool",
        "toolresult",
        "tool_result",
    }:
        return role
    data_role = str(data.get("role") or data.get("speaker") or "").lower().strip()
    if data_role:
        return data_role
    event_type = _event_type(obj).lower()
    if "user" in event_type:
        return "user"
    if (
        "assistant" in event_type
        or "agent" in event_type
        or event_type
        in {"model.completed", "model_completion", "message.completed"}
    ):
        return "assistant"
    if (
        "tool" in event_type
        or obj.get("toolName")
        or obj.get("tool_name")
        or data.get("toolName")
        or data.get("tool_name")
    ):
        return "tool"
    return role


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_value(x) for x in value if x is not None)
    if isinstance(value, dict):
        for key in (
            "text",
            "content",
            "message",
            "query",
            "prompt",
            "input",
            "result",
            "error",
        ):
            if key in value and value.get(key) is not None:
                return _text_value(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _event_content_value(obj: dict[str, Any]) -> Any:
    """Return event content while keeping prompt metadata separate from replies."""
    event_type = _event_type(obj).lower()
    data = _event_data(obj)
    containers = (data, obj)
    if event_type in {
        "model.completed",
        "model_completion",
        "message.completed",
        "message.assistant",
    }:
        for container in containers:
            for key in (
                "assistantTexts",
                "assistant_texts",
                "content",
                "text",
                "message",
            ):
                if key in container and container.get(key) is not None:
                    return container[key]
        return ""
    if event_type == "trace.artifacts":
        return ""
    final_prompt = _extract_final_prompt_text(obj)
    if final_prompt:
        return final_prompt
    for container in containers:
        for key in (
            "content",
            "text",
            "message",
            "result",
            "error",
            "query",
            "prompt",
            "input",
        ):
            if key in container and container.get(key) is not None:
                return container[key]
    return "" if _is_trajectory_metadata(obj) else json.dumps(obj, ensure_ascii=False)


def _looks_like_message_text(text: str) -> bool:
    value = clean_query(text)
    if len(value) < 2:
        return False
    if value.startswith("{") and any(
        token in value[:300]
        for token in ("traceSchema", "sessionId", "schemaVersion")
    ):
        return False
    if value.startswith("<environment_context>"):
        return False
    return True


def _unwrap(obj: Any) -> Any:
    if isinstance(obj, dict) and isinstance(obj.get("content"), dict):
        return obj["content"]
    return obj


def _first_value(objects: list[dict[str, Any]], *keys: str) -> str:
    for obj in objects:
        for key in keys:
            value = obj.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _normalise_message(obj: dict[str, Any], idx: int) -> dict[str, Any]:
    role = _event_role(obj)
    lower = role.lower()
    data = _event_data(obj)
    content = _event_content_value(obj)
    if not content:
        return {}
    if (
        "tool" in lower
        or obj.get("toolName")
        or obj.get("tool_name")
        or data.get("toolName")
        or data.get("tool_name")
    ):
        tool_name = (
            obj.get("toolName")
            or obj.get("tool_name")
            or data.get("toolName")
            or data.get("tool_name")
            or obj.get("name")
            or "unknown"
        )
        msg = {
            "idx": idx,
            "role": "toolResult",
            "toolName": str(tool_name),
            "content": content,
        }
        if _looks_error(obj):
            msg["isError"] = True
        return msg
    if lower in {"user", "human"} or obj.get("user"):
        return {"idx": idx, "role": "user", "content": content}
    if lower in {"assistant", "agent", "bot"}:
        return {"idx": idx, "role": "assistant", "content": content}
    if obj.get("name") and (obj.get("arguments") or obj.get("args")):
        return {
            "idx": idx,
            "role": "assistant",
            "content": [
                {
                    "name": obj.get("name"),
                    "arguments": obj.get("arguments") or obj.get("args") or {},
                }
            ],
        }
    if _is_trajectory_metadata(obj):
        return {}
    return {"idx": idx, "role": "assistant", "content": content}


def _looks_error(obj: dict[str, Any]) -> bool:
    if obj.get("isError") is True:
        return True
    text = json.dumps(obj, ensure_ascii=False).lower()
    return bool(re.search(r"error|exception|traceback|failed|失败|报错", text))


def _fill_judge_signal_defaults(payload: dict[str, Any]) -> None:
    messages = payload.get("messages", [])
    if isinstance(messages, str):
        parsed = _safe_json_parse(messages)
        messages = parsed if isinstance(parsed, list) else []
        payload["messages"] = messages
    if not isinstance(messages, list):
        messages = []
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("idx") is None:
            msg["idx"] = i
    user_cnt = sum(
        1 for m in messages if isinstance(m, dict) and m.get("role") == "user"
    )
    asst_cnt = sum(
        1
        for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant"
    )
    tool_cnt = sum(
        1
        for m in messages
        if isinstance(m, dict) and m.get("role") == "toolResult"
    )
    payload.setdefault("userMsgCnt", user_cnt)
    payload.setdefault("assistantMsgCnt", asst_cnt)
    payload.setdefault("toolResultCnt", tool_cnt)
    payload.setdefault("isSingleTurn", user_cnt <= 1)
    payload.setdefault("allExeSkill", _join_tool_names(messages, skill_only=True))
    payload.setdefault("allExeSkillIdx", _join_tool_indices(messages, skill_only=True))
    payload.setdefault("allExeMcp", _join_tool_names(messages, skill_only=False))
    payload.setdefault("allExeMcpIdx", _join_tool_indices(messages, skill_only=False))
    payload.setdefault("stopReasonStats", {})
    payload.setdefault("toolResultStatusDistribution", {})
    payload.setdefault("toolUseStats", {})
    payload.setdefault("execCommandStats", {})


def _message_tool_names(msg: dict[str, Any]) -> list[str]:
    names: list[str] = []
    direct = str(msg.get("toolName") or msg.get("tool_name") or "").strip()
    if direct:
        names.append(direct)
    content = msg.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("name"):
                name = str(item["name"]).strip()
                if name and name not in names:
                    names.append(name)
    return names


def _is_mcp_name(name: str) -> bool:
    return name.startswith("mcp.") or name.startswith("mcp_")


def _join_tool_names(messages: list[Any], skill_only: bool) -> str:
    names: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for name in _message_tool_names(msg):
            if not name or name in names:
                continue
            if skill_only and not _is_mcp_name(name):
                names.append(name)
            elif not skill_only and _is_mcp_name(name):
                names.append(name)
    return ",".join(names)


def _join_tool_indices(messages: list[Any], skill_only: bool) -> str:
    pairs: list[str] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        for name in _message_tool_names(msg):
            if not name:
                continue
            if (skill_only and not _is_mcp_name(name)) or (
                not skill_only and _is_mcp_name(name)
            ):
                pairs.append(str(msg.get("idx", i)))
    return ",".join(pairs)


def _safe_json_parse(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None
