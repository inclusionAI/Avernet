from __future__ import annotations

import heapq
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .. import logger
from ..constants import SESSION_USER_PROMPT_EXCLUDED_PREFIXES
from ..judge.keyless_session_prompt import session_analysis_prompt_prefix
from ..judge.ocsa_session_adapter import build_judge_session_payload
from ..models import SessionRow
from ..utils import clean_query


DEFAULT_SESSION_RAW_TEXT_LIMIT = 12000


def _default_int(default: int) -> int:
    return default


def _unwrap(obj: Any) -> Any:
    if isinstance(obj, dict) and isinstance(obj.get("content"), dict):
        return obj["content"]
    return obj


def _text_of(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "\n".join(_text_of(x) for x in obj)
    if isinstance(obj, dict):
        for k in ("text", "content", "message", "query", "prompt", "error"):
            if k in obj:
                return _text_of(obj[k])
        return json.dumps(obj, ensure_ascii=False)
    return str(obj)


def _role(obj: dict[str, Any]) -> str:
    return str(obj.get("role") or obj.get("type") or obj.get("speaker") or "").lower()




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


def _is_trajectory_metadata(obj: dict[str, Any]) -> bool:
    event_type = _event_type(obj).lower()
    if event_type in _TRAJECTORY_METADATA_TYPES:
        return True
    if event_type == "trace.artifacts":
        return True
    return False


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
        candidates.extend([artifacts.get("finalPromptText"), artifacts.get("final_prompt_text")])
    for value in candidates:
        text = clean_query(_text_of(value))
        if _looks_like_user_query(text):
            return text
    return ""


def _extract_raw_final_prompt_text(obj: dict[str, Any]) -> str:
    data = _event_data(obj)
    candidates = [
        data.get("finalPromptText"),
        data.get("final_prompt_text"),
        obj.get("finalPromptText"),
        obj.get("final_prompt_text"),
    ]
    artifacts = data.get("artifacts") or obj.get("artifacts")
    if isinstance(artifacts, dict):
        candidates.extend([artifacts.get("finalPromptText"), artifacts.get("final_prompt_text")])
    for value in candidates:
        text = _text_of(value).strip()
        if text:
            return text
    return ""


def _extract_raw_message_text(obj: dict[str, Any]) -> str:
    data = _event_data(obj)
    for container in (obj, data):
        for key in ("assistantTexts", "assistant_texts", "text", "content", "message", "query", "prompt", "input"):
            if key in container and container.get(key) is not None:
                text = _text_of(container.get(key)).strip()
                if text:
                    return text
    return _text_of(obj).strip()


def _raw_event_role(obj: dict[str, Any]) -> str:
    """Classify trajectory events without confusing prompt metadata with roles."""
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
    if _extract_raw_final_prompt_text(obj):
        return "user"
    role = _role(obj)
    if role in {"user", "human", "assistant", "agent", "bot", "tool", "toolresult", "tool_result"}:
        return role
    data = _event_data(obj)
    data_role = str(data.get("role") or data.get("speaker") or "").lower().strip()
    if data_role:
        return data_role
    event_type = _event_type(obj).lower()
    if "user" in event_type or event_type in {"message.user", "user_message"}:
        return "user"
    if "assistant" in event_type or "agent" in event_type or event_type in {"message.assistant", "assistant_message", "model.completed", "model_completion", "message.completed"}:
        return "assistant"
    if "tool" in event_type or obj.get("toolName") or obj.get("tool_name") or data.get("toolName") or data.get("tool_name"):
        return "tool"
    return role


def _first_raw_user_text(events: list[dict[str, Any]]) -> str:
    for event in events:
        final_prompt = _extract_raw_final_prompt_text(event)
        if final_prompt:
            return final_prompt
        role = _raw_event_role(event)
        if role in {"user", "human"} or event.get("user"):
            text = _extract_raw_message_text(event)
            if text:
                return text
    return ""


def _matches_excluded_user_prompt_prefix(text: str) -> bool:
    """Return whether parsed user text starts with a configured internal prompt prefix."""

    normalized_text = _normalize_prompt_prefix_raw(text)
    for prefix in _excluded_user_prompt_prefixes():
        normalized_prefix = _normalize_prompt_prefix_raw(prefix)
        if normalized_prefix and normalized_text.startswith(normalized_prefix):
            return True
    return False


def _normalize_prompt_prefix_raw(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _excluded_user_prompt_prefixes() -> tuple[str, ...]:
    """Return dynamic and configured prefixes for internal helper sessions."""

    dynamic_prefixes = (session_analysis_prompt_prefix(),)
    return tuple(
        prefix
        for prefix in (*dynamic_prefixes, *SESSION_USER_PROMPT_EXCLUDED_PREFIXES)
        if str(prefix or "").strip()
    )


def _should_skip_by_first_question(
    first_question: str,
    path: Path,
    source: str,
    session_id: str = "",
) -> bool:
    if not first_question or not _matches_excluded_user_prompt_prefix(first_question):
        return False
    logger.info(
        "diagnose internal session skipped by first question prefix",
        path=path,
        source=source,
        session_id=session_id,
        reason="first_user_prompt_matches_excluded_prefix",
        first_question_preview=_normalize_prompt_prefix_raw(first_question)[:200],
    )
    return True


def _looks_like_user_query(text: str) -> bool:
    value = clean_query(text)
    if len(value) < 2:
        return False
    if value.startswith("{") and any(
        token in value[:300] for token in ("traceSchema", "sessionId", "schemaVersion")
    ):
        return False
    if value.startswith("<environment_context>"):
        return False
    if re.fullmatch(r"[-=_\s]+", value):
        return False
    return True


def _extract_message_text(obj: dict[str, Any]) -> str:
    # OpenClaw trajectory events often store the real message under data.* while
    # the top-level event is metadata.  Prefer semantically named message fields
    # and only fall back to raw object JSON for non-metadata events.
    data = _event_data(obj)
    for container in (obj, data):
        for key in ("assistantTexts", "assistant_texts", "text", "content", "message", "query", "prompt", "input"):
            if key in container and container.get(key) is not None:
                text = _text_of(container.get(key))
                if text:
                    return text
    return "" if _is_trajectory_metadata(obj) else _text_of(obj)


def _extract_role(obj: dict[str, Any]) -> str:
    if _extract_final_prompt_text(obj):
        return "user"
    role = _role(obj)
    if role in {"user", "human", "assistant", "agent", "bot", "tool", "toolresult", "tool_result"}:
        return role
    data = _event_data(obj)
    data_role = str(data.get("role") or data.get("speaker") or "").lower().strip()
    if data_role:
        return data_role
    event_type = _event_type(obj).lower()
    if "user" in event_type or event_type in {"message.user", "user_message"}:
        return "user"
    if "assistant" in event_type or "agent" in event_type or event_type in {"message.assistant", "assistant_message", "model.completed", "model_completion", "message.completed"}:
        return "assistant"
    if "tool" in event_type or obj.get("toolName") or obj.get("tool_name") or data.get("toolName") or data.get("tool_name"):
        return "tool"
    return role


def _session_id(path: Path, events: list[dict[str, Any]]) -> str:
    for e in events:
        for k in ("session_id", "sessionId", "conversationId", "id"):
            if e.get(k):
                return str(e[k])
    return path.stem


_MODEL_KEYS = {
    "model",
    "model_name",
    "modelName",
    "model_id",
    "modelId",
    "llm_model",
    "llmModel",
    "chat_model",
    "chatModel",
    "base_model",
    "baseModel",
    "modelVersion",
    "model_version",
}


def _model_from_value(value: Any, path: str = "") -> tuple[str, str]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if (
                key in _MODEL_KEYS
                and isinstance(child, (str, int, float))
                and str(child).strip()
            ):
                return str(child).strip(), child_path
        for key, child in value.items():
            found, source = _model_from_value(
                child, f"{path}.{key}" if path else str(key)
            )
            if found:
                return found, source
    elif isinstance(value, list):
        for index, child in enumerate(value[:20]):
            found, source = _model_from_value(child, f"{path}[{index}]")
            if found:
                return found, source
    return "", ""


def _original_model(events: list[dict[str, Any]]) -> tuple[str, str]:
    # Prefer the model that produced the original assistant/bot response. Fall back to
    # explicit model-invocation metadata, then any session-level model field.
    ordered = sorted(
        enumerate(events), key=lambda pair: (_model_event_priority(pair[1]), pair[0])
    )
    for index, event in ordered:
        value, source = _model_from_value(event)
        if value:
            return value, f"event[{index}].{source}"
    return "", ""


def _model_event_priority(event: dict[str, Any]) -> int:
    role = _role(event)
    if role in {"assistant", "agent", "bot"}:
        return 0
    event_type = str(
        event.get("type") or event.get("event") or event.get("name") or ""
    ).lower()
    if any(token in event_type for token in ("model", "llm", "completion", "chat")):
        return 1
    if any(key in event for key in _MODEL_KEYS):
        return 2
    if "model" in json.dumps(event, ensure_ascii=False)[:1000].lower():
        return 3
    return 4


def _bot_id(events: list[dict[str, Any]]) -> str:
    for e in events:
        for k in ("bot_id", "botId", "bot_uuid"):
            if e.get(k):
                return str(e[k]).split(":", 1)[0]
    return ""


@dataclass(frozen=True)
class _SessionFileParseResult:
    rows: list[SessionRow]
    skipped_by_excluded_first_question: bool = False


def parse_jsonl_file(path: Path, row_limit: int | None = None) -> list[SessionRow]:
    """Parse a local JSONL session file and preserve complete OCSA evidence."""

    return _parse_jsonl_file(path, row_limit).rows


def _parse_jsonl_file(
    path: Path, row_limit: int | None = None
) -> _SessionFileParseResult:
    """Parse a JSONL session file and preserve intentional prompt-filter state.

    Empty rows can mean either unreadable/empty/unparseable input or an intentionally
    skipped internal helper transcript.  Session-store discovery needs that
    distinction so a filtered transcript cannot be reintroduced via metadata label
    fallback.
    """

    rows: list[SessionRow] = []
    events: list[dict[str, Any]] = []

    try:
        handle = path.open(encoding="utf-8", errors="ignore")
    except OSError:
        return _SessionFileParseResult([])

    with handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                obj = _unwrap(json.loads(line))
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue

            if _looks_like_session_record(obj):
                first_question = _first_question_from_session_record(obj)
                if _should_skip_by_first_question(first_question, path, "session_record"):
                    return _SessionFileParseResult(
                        [], skipped_by_excluded_first_question=True
                    )
                row = _session_row_from_record(path, obj, index)
                if row is not None:
                    rows.append(row)
                    if row_limit is not None and len(rows) >= row_limit:
                        return _SessionFileParseResult(rows)
                continue

            events.append(obj)

    if rows:
        limited = rows[:row_limit] if row_limit is not None else rows
        return _SessionFileParseResult(limited)

    if not events:
        return _SessionFileParseResult([])
    first_question = _first_question_from_events(events)
    if _should_skip_by_first_question(first_question, path, "event_file"):
        return _SessionFileParseResult([], skipped_by_excluded_first_question=True)
    parsed = _session_rows_from_event_file(path, events)
    limited = parsed[:row_limit] if row_limit is not None else parsed
    return _SessionFileParseResult(limited)

def _looks_like_session_record_file(records: list[dict[str, Any]]) -> bool:
    if not records:
        return False
    session_like = sum(1 for record in records if _looks_like_session_record(record))
    return session_like > 0 and session_like >= max(1, len(records) // 2)


def _looks_like_session_record(record: dict[str, Any]) -> bool:
    if isinstance(record.get("messages"), (list, str)):
        return True
    session_keys = {"session_id", "sessionId"} & set(record.keys())
    aggregate_keys = {
        "user_msg_cnt",
        "assistant_msg_cnt",
        "tool_result_cnt",
        "userMsgCnt",
        "assistantMsgCnt",
        "toolResultCnt",
        "all_exe_skill",
        "allExeSkill",
        "all_exe_mcp",
        "allExeMcp",
    } & set(record.keys())
    return bool(session_keys and aggregate_keys)


def _session_row_from_record(path: Path, record: dict[str, Any], index: int) -> SessionRow | None:
    first_raw_user_text = ""
    raw_messages = record.get("messages")
    if isinstance(raw_messages, str):
        try:
            raw_messages = json.loads(raw_messages)
        except Exception:
            raw_messages = []
    if isinstance(raw_messages, list):
        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            role = _role(msg)
            text = _text_of(msg)
            if role in {"user", "human"} or msg.get("user"):
                if text:
                    first_raw_user_text = text
                    break
    payload = build_judge_session_payload([record])
    messages = payload.get("messages", [])
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except Exception:
            messages = []
    if not isinstance(messages, list):
        messages = []

    user_parts: list[str] = []
    assistant_parts: list[str] = []
    tool_parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = _role(msg)
        text = _text_of(msg)
        if role in {"user", "human"} or msg.get("user"):
            user_parts.append(text)
        elif "tool" in role or msg.get("toolName") or msg.get("tool_name"):
            tool_parts.append(text)
        elif role in {"assistant", "agent", "bot"}:
            assistant_parts.append(text)

    query = str(record.get("query") or payload.get("query") or "")
    raw_first_question = query or first_raw_user_text
    first_question = clean_query(raw_first_question or (user_parts[0] if user_parts else ""))
    if not first_question:
        return None
    if _should_skip_by_first_question(first_question, path, "session_record"):
        return None
    original_model, original_model_source = _original_model([record, payload])
    session_id = str(
        payload.get("session_id")
        or payload.get("sessionId")
        or record.get("session_id")
        or record.get("sessionId")
        or f"{path.stem}:{index}"
    )
    return SessionRow(
        session_id=session_id,
        path=str(path),
        bot_id=str(payload.get("botId") or payload.get("bot_id") or record.get("bot_id") or record.get("botId") or ""),
        created_at=str(
            payload.get("start_time")
            or payload.get("created_at")
            or record.get("start_time")
            or record.get("created_at")
            or record.get("timestamp")
            or ""
        ),
        first_question=first_question,
        user_text="\n".join(user_parts) or first_question,
        assistant_text="\n".join(assistant_parts),
        tool_text="\n".join(tool_parts),
        raw_text=json.dumps(record, ensure_ascii=False)[:_default_int(DEFAULT_SESSION_RAW_TEXT_LIMIT)],
        original_model=original_model,
        original_model_source=original_model_source,
        raw_session=payload,
    )


def _first_question_from_events(events: list[dict[str, Any]]) -> str:
    raw_first_question = _first_raw_user_text(events)
    if raw_first_question:
        return clean_query(raw_first_question)
    final_prompt = ""
    user_parts: list[str] = []
    for e in events:
        if not final_prompt:
            final_prompt = _extract_raw_final_prompt_text(e)
        role = _extract_role(e)
        text = _extract_message_text(e)
        if role in {"user", "human"} or e.get("user"):
            if _looks_like_user_query(text):
                user_parts.append(text)
    return clean_query(final_prompt or (user_parts[0] if user_parts else ""))


def _session_rows_from_event_file(path: Path, events: list[dict[str, Any]]) -> list[SessionRow]:
    first_question = _first_question_from_events(events)

    user_parts: list[str] = []
    assistant_parts: list[str] = []
    tool_parts: list[str] = []
    final_prompt = ""
    created_at = ""
    for e in events:
        if not created_at:
            created_at = str(e.get("created_at") or e.get("timestamp") or e.get("ts") or "")
        if not final_prompt:
            final_prompt = _extract_raw_final_prompt_text(e)
        role = _extract_role(e)
        text = _extract_message_text(e)
        if role in {"user", "human"} or e.get("user"):
            if _looks_like_user_query(text):
                user_parts.append(text)
        elif "tool" in role or e.get("toolName") or e.get("tool_name"):
            if text:
                tool_parts.append(text)
        elif role in {"assistant", "agent", "bot"}:
            if text:
                assistant_parts.append(text)
        elif text and re.search(r"error|exception|traceback|failed|失败|报错", text, re.I):
            tool_parts.append(text)
    if final_prompt and (not user_parts or user_parts[0] != final_prompt):
        user_parts.insert(0, final_prompt)
    user_text = "\n".join(user_parts)
    if not _looks_like_user_query(first_question):
        return []
    original_model, original_model_source = _original_model(events)
    raw_session = build_judge_session_payload(events)
    return [
        SessionRow(
            session_id=_session_id(path, events),
            path=str(path),
            bot_id=_bot_id(events),
            created_at=created_at,
            first_question=first_question,
            user_text=user_text,
            assistant_text="\n".join(assistant_parts),
            tool_text="\n".join(tool_parts),
            raw_text=("\n".join(json.dumps(e, ensure_ascii=False) for e in events[-80:]))[-_default_int(DEFAULT_SESSION_RAW_TEXT_LIMIT):],
            original_model=original_model,
            original_model_source=original_model_source,
            raw_session=raw_session,
        )
    ]



@dataclass(frozen=True)
class _SessionStoreCandidate:
    """A user session candidate selected from OpenClaw sessions metadata."""

    session_id: str
    path: Path
    started_at: datetime
    label: str = ""
    model: str = ""
    store_key: str = ""


def discover_sessions(
    layout: dict[str, Any],
    max_sessions: int = 1200,
    since: str = "",
    until: str = "",
    *,
    parse_content: bool = True,
) -> list[SessionRow]:
    """Discover local user sessions from sessions.json only, newest first."""

    time_window = _DiscoveryTimeWindow.from_raw(since, until)
    roots = [Path(root).expanduser() for root in layout.get("session_dirs", [])]
    logger.info(
        "session discovery configured",
        roots=len(roots),
        max_sessions=max_sessions,
        since=since,
        until=until,
        parsed_since=time_window.since.isoformat() if time_window.since else "",
        parsed_until=time_window.until.isoformat() if time_window.until else "",
        time_window_enabled=time_window.enabled,
        discovery_source="sessions_json_only",
        file_order="session_store_sessionStartedAt_desc",
        jsonl_directory_scan_fallback=False,
        parse_content=parse_content,
    )

    store_rows, store_stats = _discover_sessions_from_session_stores(
        roots, max_sessions, time_window, parse_content=parse_content
    )
    logger.info(
        "session discovery complete from sessions store",
        rows=len(store_rows),
        max_sessions=max_sessions,
        newest_created_at=store_rows[0].created_at if store_rows else "",
        oldest_created_at=store_rows[-1].created_at if store_rows else "",
        **store_stats,
    )
    if store_stats.get("store_files_seen", 0) <= 0:
        logger.warning(
            "sessions store not found; jsonl directory scan fallback disabled",
            max_sessions=max_sessions,
            roots=len(roots),
        )
    return store_rows


def _discover_sessions_from_session_stores(
    roots: list[Path],
    max_sessions: int,
    time_window: "_DiscoveryTimeWindow",
    *,
    parse_content: bool = True,
) -> tuple[list[SessionRow], dict[str, Any]]:
    """Build session rows from OpenClaw sessions.json/session.json stores."""

    stats: dict[str, Any] = {
        "store_files_seen": 0,
        "store_entries_seen": 0,
        "store_user_entries": 0,
        "store_time_filtered": {},
        "store_missing_path": 0,
        "store_duplicate_skipped": 0,
        "store_parse_empty": 0,
        "store_rows_from_metadata_only": 0,
        "store_prompt_filtered_or_empty": 0,
    }
    candidates: list[_SessionStoreCandidate] = []
    seen_candidate_keys: set[str] = set()

    for root in roots:
        for store_path in _session_store_paths(root):
            if not store_path.exists():
                logger.debug("session store path skipped missing", root=root, store_path=store_path)
                continue
            stats["store_files_seen"] += 1
            logger.info("session store read start", root=root, store_path=store_path)
            for store_key, record in _load_session_store_entries(store_path):
                stats["store_entries_seen"] += 1
                candidate = _session_store_candidate(store_key, record, root)
                if candidate is None:
                    continue
                stats["store_user_entries"] += 1
                in_window, reason = time_window.contains(candidate.started_at)
                if not in_window:
                    filtered = stats["store_time_filtered"]
                    filtered[reason] = filtered.get(reason, 0) + 1
                    continue
                key = candidate.session_id or str(candidate.path)
                if key in seen_candidate_keys:
                    stats["store_duplicate_skipped"] += 1
                    continue
                seen_candidate_keys.add(key)
                candidates.append(candidate)
            logger.info(
                "session store read done",
                root=root,
                store_path=store_path,
                store_entries_seen=stats["store_entries_seen"],
                store_user_entries=stats["store_user_entries"],
            )

    candidates.sort(
        key=lambda candidate: (candidate.started_at.timestamp(), candidate.session_id),
        reverse=True,
    )
    logger.info(
        "session store candidates prepared",
        candidates=len(candidates),
        candidate_preview=[
            {
                "session_id": c.session_id,
                "started_at": c.started_at.isoformat(),
                "path": str(c.path),
                "label_preview": clean_query(_clean_store_label(c.label), max_len=160),
            }
            for c in candidates[:5]
        ],
        **stats,
    )

    rows: list[SessionRow] = []
    seen_rows: set[str] = set()
    for candidate in candidates:
        if len(rows) >= max_sessions:
            break
        if not candidate.path.exists() or not candidate.path.is_file():
            stats["store_missing_path"] += 1
            continue
        if not parse_content:
            label = _clean_store_label(candidate.label)
            if label and _should_skip_by_first_question(
                label,
                candidate.path,
                "session_store_metadata",
                candidate.session_id,
            ):
                stats["store_prompt_filtered_or_empty"] += 1
                continue
            row = _session_locator_from_store_candidate(candidate)
            key = _session_dedupe_key(row)
            if key in seen_rows:
                stats["store_duplicate_skipped"] += 1
                continue
            seen_rows.add(key)
            rows.append(row)
            stats["store_rows_from_metadata_only"] += 1
            logger.debug(
                "session locator accepted without content parsing",
                session_id=row.session_id,
                path=row.path,
                created_at=row.created_at,
                total_rows=len(rows),
            )
            continue
        logger.debug(
            "session transcript parse start",
            session_id=candidate.session_id,
            path=candidate.path,
            started_at=candidate.started_at.isoformat(),
        )
        parse_result = _parse_jsonl_file(candidate.path, row_limit=1)
        parsed_rows = parse_result.rows
        if not parsed_rows:
            if parse_result.skipped_by_excluded_first_question:
                stats["store_prompt_filtered_or_empty"] += 1
                continue
            stats["store_parse_empty"] += 1
            label = _clean_store_label(candidate.label)
            if label and _should_skip_by_first_question(
                label,
                candidate.path,
                "session_store_metadata",
                candidate.session_id,
            ):
                stats["store_prompt_filtered_or_empty"] += 1
                continue
            metadata_row = _session_row_from_store_candidate(candidate)
            parsed_rows = [metadata_row]
            stats["store_rows_from_metadata_only"] += 1

        for row in parsed_rows:
            row = _merge_store_metadata(row, candidate)
            if row.first_question and _matches_excluded_user_prompt_prefix(row.first_question):
                stats["store_prompt_filtered_or_empty"] += 1
                logger.info(
                    "session row skipped by cleaned first question fallback",
                    session_id=row.session_id,
                    path=row.path,
                    first_question_preview=row.first_question[:200],
                )
                continue
            key = _session_dedupe_key(row)
            if key in seen_rows:
                stats["store_duplicate_skipped"] += 1
                continue
            seen_rows.add(key)
            rows.append(row)
            logger.debug(
                "session row accepted",
                session_id=row.session_id,
                path=row.path,
                created_at=row.created_at,
                first_question_preview=row.first_question[:200],
                total_rows=len(rows),
            )
            break

    return _sort_session_rows_newest(rows[:max_sessions]), stats


def session_locator_from_path(path: Path) -> SessionRow | None:
    """Build a locator for an explicitly selected raw Session without parsing it."""

    candidate = path.expanduser()
    if candidate.is_symlink():
        return None
    source = candidate.resolve(strict=False)
    if not source.exists() or not source.is_file():
        return None
    try:
        created_at = datetime.fromtimestamp(
            source.stat().st_mtime, tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")
    except OSError:
        created_at = ""
    return SessionRow(
        session_id=source.name.split(".jsonl", 1)[0] or source.stem,
        path=str(source),
        bot_id="",
        created_at=created_at,
        first_question="",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
        raw_session={},
    )



def _first_question_from_session_record(record: dict[str, Any]) -> str:
    raw_query = str(record.get("query") or "")
    first_raw_user_text = ""
    raw_messages = record.get("messages")
    if isinstance(raw_messages, str):
        try:
            raw_messages = json.loads(raw_messages)
        except Exception:
            raw_messages = []
    if isinstance(raw_messages, list):
        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            role = _role(msg)
            text = _text_of(msg)
            if role in {"user", "human"} or msg.get("user"):
                if text:
                    first_raw_user_text = text
                    break
    return clean_query(raw_query or first_raw_user_text)


def _session_store_paths(root: Path) -> list[Path]:
    return [root / "sessions.json", root / "session.json"]


def _load_session_store_entries(store_path: Path) -> list[tuple[str, dict[str, Any]]]:
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "session store read failed",
            path=store_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        return []
    entries = list(_iter_session_store_entries(payload))
    logger.info("session store parsed", path=store_path, entries=len(entries))
    return entries


def _iter_session_store_entries(value: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if _looks_like_session_store_record(value):
            return [("", value)]
        entries: list[tuple[str, dict[str, Any]]] = []
        for key, child in value.items():
            if isinstance(child, dict) and _looks_like_session_store_record(child):
                entries.append((str(key), child))
            elif isinstance(child, (dict, list)):
                entries.extend(_iter_session_store_entries(child))
        return entries
    if isinstance(value, list):
        entries = []
        for child in value:
            entries.extend(_iter_session_store_entries(child))
        return entries
    return []


def _looks_like_session_store_record(record: dict[str, Any]) -> bool:
    return bool(record.get("sessionFile") or record.get("session_file")) and bool(
        record.get("sessionId") or record.get("session_id") or record.get("id")
    )


def _session_store_candidate(
    store_key: str, record: dict[str, Any], root: Path
) -> _SessionStoreCandidate | None:
    if not _is_user_session_store_entry(store_key, record):
        return None
    session_id = str(
        record.get("sessionId") or record.get("session_id") or record.get("id") or ""
    ).strip()
    path_text = str(
        record.get("sessionFile") or record.get("session_file") or ""
    ).strip()
    if not session_id or not path_text:
        logger.debug("session store entry skipped incomplete", store_key=store_key, session_id=session_id, path_text=path_text)
        return None
    dt = _metadata_datetime(record)
    if dt is None:
        logger.debug("session store entry skipped missing time", store_key=store_key, session_id=session_id, path_text=path_text)
        return None
    path = _resolve_session_store_file_path(path_text, root)
    label = str(record.get("label") or record.get("title") or record.get("name") or "")
    if _should_skip_by_first_question(label, path, "session_store_label", session_id):
        return None
    return _SessionStoreCandidate(
        session_id=session_id,
        path=path,
        started_at=dt,
        label=label,
        model=str(
            record.get("model")
            or record.get("modelName")
            or record.get("model_name")
            or ""
        ),
        store_key=store_key,
    )


def _resolve_session_store_file_path(path_text: str, root: Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        return _prefer_trajectory_file(root / path)
    if path.exists():
        return _prefer_trajectory_file(path)
    local_path = root / path.name
    if local_path.exists():
        remapped = _prefer_trajectory_file(local_path)
        logger.info(
            "session store path remapped for local openclaw fixture",
            original_path=path,
            remapped_path=remapped,
        )
        return remapped
    return path


def _prefer_trajectory_file(path: Path) -> Path:
    if path.name.endswith(".trajectory.jsonl"):
        return path
    if path.suffix != ".jsonl":
        return path
    trajectory_path = path.with_name(f"{path.stem}.trajectory.jsonl")
    if trajectory_path.exists():
        logger.info(
            "session store transcript switched to trajectory file",
            session_file=path,
            trajectory_file=trajectory_path,
        )
        return trajectory_path
    return path


def _is_user_session_store_entry(store_key: str, record: dict[str, Any]) -> bool:
    key = str(store_key or "").lower()
    if any(marker in key for marker in (":subagent:", ":cron:", ":spawned:")):
        return False
    if (
        record.get("spawnedBy")
        or record.get("spawnDepth") is not None
        or record.get("subagentRole")
    ):
        return False
    if not key or ":session:" in key:
        return True
    if not key.startswith("agent:main:"):
        return False

    chat_type = str(record.get("chatType") or "").lower()
    origin = record.get("origin") if isinstance(record.get("origin"), dict) else {}
    delivery = (
        record.get("deliveryContext")
        if isinstance(record.get("deliveryContext"), dict)
        else {}
    )
    provider = str(origin.get("provider") or "").lower()
    channel = str(delivery.get("channel") or record.get("lastChannel") or "").lower()
    return chat_type in {"direct", "group"} or provider in {"webchat", "cli"} or bool(channel)

def _session_row_from_store_candidate(candidate: _SessionStoreCandidate) -> SessionRow:
    first_question = candidate.label.strip()
    if _should_skip_by_first_question(
        first_question, candidate.path, "session_store_metadata", candidate.session_id
    ):
        first_question = ""
    return SessionRow(
        session_id=candidate.session_id,
        path=str(candidate.path),
        bot_id="",
        created_at=candidate.started_at.isoformat().replace("+00:00", "Z"),
        first_question=first_question,
        user_text=first_question,
        assistant_text="",
        tool_text="",
        raw_text=json.dumps(
            {
                "sessionId": candidate.session_id,
                "sessionFile": str(candidate.path),
                "sessionStartedAt": candidate.started_at.isoformat(),
                "label": candidate.label,
                "model": candidate.model,
            },
            ensure_ascii=False,
        )[:_default_int(DEFAULT_SESSION_RAW_TEXT_LIMIT)],
        original_model=candidate.model,
        original_model_source="sessions.json.model" if candidate.model else "",
        raw_session={},
    )


def _session_locator_from_store_candidate(
    candidate: _SessionStoreCandidate,
) -> SessionRow:
    return SessionRow(
        session_id=candidate.session_id,
        path=str(candidate.path),
        bot_id="",
        created_at=candidate.started_at.isoformat().replace("+00:00", "Z"),
        first_question="",
        user_text="",
        assistant_text="",
        tool_text="",
        raw_text="",
        original_model=candidate.model,
        original_model_source="sessions.json.model" if candidate.model else "",
        raw_session={},
    )


def _merge_store_metadata(row: SessionRow, candidate: _SessionStoreCandidate) -> SessionRow:
    row.session_id = candidate.session_id or row.session_id
    row.path = str(candidate.path)
    row.created_at = candidate.started_at.isoformat().replace("+00:00", "Z")
    raw_label = row.first_question or candidate.label
    if _should_skip_by_first_question(
        raw_label, candidate.path, "session_store_merge", candidate.session_id
    ):
        row.first_question = ""
        row.user_text = ""
        return row
    row.first_question = _clean_store_label(row.first_question) or _clean_store_label(
        candidate.label
    )
    if row.user_text:
        row.user_text = _clean_store_label(row.user_text)
    else:
        row.user_text = row.first_question
    if candidate.model and not row.original_model:
        row.original_model = candidate.model
        row.original_model_source = "sessions.json.model"
    return row


def _clean_store_label(label: str) -> str:
    text = clean_query(label or "")
    # OpenClaw labels can append the store key after the user-visible text.
    return re.sub(r"_agent:[^\s]+$", "", text).strip()


def _discover_sessions_from_jsonl_scan(
    roots: list[Path],
    max_sessions: int,
    time_window: "_DiscoveryTimeWindow",
) -> list[SessionRow]:
    rows: list[SessionRow] = []
    seen_paths: set[str] = set()
    seen_session_keys: set[str] = set()
    duplicate_rows_skipped = 0
    row_time_skipped = {"missing_time": 0, "before_since": 0, "after_until": 0}
    file_limit = _session_file_scan_limit(max_sessions, time_window.enabled)

    file_candidates: list[Path] = []
    file_time_index: dict[str, datetime] = {}
    for root_index, p in enumerate(roots, start=1):
        if not p.exists():
            logger.info(
                "session root skipped",
                root_index=f"{root_index}/{len(roots)}",
                root=p,
                reason="missing",
            )
            continue
        logger.info(
            "session root jsonl fallback scan start",
            root_index=f"{root_index}/{len(roots)}",
            root=p,
        )
        files, root_time_index, root_stats = _recent_jsonl_files(p, file_limit, time_window)
        unique_files: list[Path] = []
        for f in files:
            key = str(f)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            unique_files.append(f)
            if key in root_time_index:
                file_time_index[key] = root_time_index[key]
        file_candidates.extend(unique_files)
        logger.info(
            "session root jsonl fallback scan done",
            root_index=f"{root_index}/{len(roots)}",
            root=p,
            jsonl_files=len(files),
            unique_jsonl_files=len(unique_files),
            **root_stats,
        )

    file_candidates.sort(
        key=lambda path: _file_effective_time_key(path, file_time_index),
        reverse=True,
    )
    logger.info("session file candidates prepared", jsonl_files=len(file_candidates))
    for file_index, f in enumerate(file_candidates, start=1):
        remaining = max_sessions - len(rows)
        if remaining <= 0:
            break
        parsed = parse_jsonl_file(f, None if time_window.enabled else remaining)
        unique_rows: list[SessionRow] = []
        for row in parsed:
            in_window, reason = time_window.contains_row(row)
            if not in_window:
                row_time_skipped[reason] = row_time_skipped.get(reason, 0) + 1
                continue
            key = _session_dedupe_key(row)
            if key in seen_session_keys:
                duplicate_rows_skipped += 1
                continue
            seen_session_keys.add(key)
            unique_rows.append(row)
            if len(rows) + len(unique_rows) >= max_sessions:
                break
        rows.extend(unique_rows)
        if parsed or file_index == 1 or file_index % 20 == 0:
            logger.info(
                "session file parsed",
                file_index=f"{file_index}/{len(file_candidates)}",
                path=f,
                parsed=len(parsed),
                unique=len(unique_rows),
                duplicate_rows_skipped=duplicate_rows_skipped,
                time_filtered_rows=row_time_skipped,
                total_rows=len(rows),
                remaining=max(0, max_sessions - len(rows)),
            )
        else:
            logger.debug("session file parsed empty", path=f)
    rows = _sort_session_rows_newest(rows)
    logger.info(
        "session discovery complete from jsonl fallback",
        rows=len(rows),
        files_seen=len(file_candidates),
        max_sessions=max_sessions,
        duplicate_rows_skipped=duplicate_rows_skipped,
        time_filtered_rows=row_time_skipped,
        newest_created_at=rows[0].created_at if rows else "",
        oldest_created_at=rows[-1].created_at if rows else "",
    )
    return rows[:max_sessions]


class _DiscoveryTimeWindow:
    """Time-window helper local to session discovery."""

    def __init__(self, since: datetime | None, until: datetime | None) -> None:
        self.since = since
        self.until = until
        self.enabled = bool(since or until)

    @classmethod
    def from_raw(cls, since: str, until: str) -> "_DiscoveryTimeWindow":
        return cls(_parse_session_datetime(since), _parse_session_datetime(until))

    def contains(self, dt: datetime | None) -> tuple[bool, str]:
        if not self.enabled:
            return True, ""
        if dt is None:
            return False, "missing_time"
        if self.since and dt < self.since:
            return False, "before_since"
        if self.until and dt > self.until:
            return False, "after_until"
        return True, ""

    def contains_row(self, row: SessionRow) -> tuple[bool, str]:
        return self.contains(_session_row_datetime(row))


def _parse_session_datetime(raw: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if re.fullmatch(r"\d{8}(?:[_-]?\d{6})?", value):
        compact = value.replace("_", "").replace("-", "")
        try:
            return datetime(
                int(compact[0:4]),
                int(compact[4:6]),
                int(compact[6:8]),
                int(compact[8:10] or 0),
                int(compact[10:12] or 0),
                int(compact[12:14] or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        try:
            ts = float(value)
            while ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    normalized = value.replace("Z", "+00:00")
    normalized = re.sub(r"\s+UTC$", "+00:00", normalized, flags=re.I)
    normalized = re.sub(r"\s+GMT$", "+00:00", normalized, flags=re.I)
    tz_match = re.search(
        r"\s+(?:UTC|GMT)([+-])(\d{1,2})(?::?(\d{2}))?$",
        normalized,
        flags=re.I,
    )
    if tz_match:
        sign, hour, minute = tz_match.groups()
        normalized = (
            normalized[: tz_match.start()]
            + f"{sign}{int(hour):02d}:{minute or '00'}"
        )
    for candidate in [normalized, normalized.replace("/", "-")]:
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    compact_match = re.match(
        r"^(\d{4})(\d{2})(\d{2})(?:[_-]?(\d{2})(\d{2})(\d{2})?)?", value
    )
    if compact_match:
        year, month, day, hour, minute, second = compact_match.groups()
        try:
            return datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                int(second or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(
                value[: len(datetime.now().strftime(fmt))], fmt
            ).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _parse_session_path_datetime(path: str) -> datetime | None:
    text = str(path or "")
    match = re.search(
        r"(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})[_-]?(\d{2})?(\d{2})?(\d{2})?",
        text,
    )
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _session_row_datetime(row: SessionRow) -> datetime | None:
    dt = _parse_session_datetime(getattr(row, "created_at", ""))
    if dt is not None:
        return dt
    dt = _parse_session_path_datetime(getattr(row, "path", ""))
    if dt is not None:
        return dt
    try:
        return datetime.fromtimestamp(
            os.path.getmtime(getattr(row, "path", "")), tz=timezone.utc
        )
    except (OSError, TypeError, ValueError):
        return None


def _sort_session_rows_newest(rows: list[SessionRow]) -> list[SessionRow]:
    return sorted(
        rows,
        key=lambda row: (
            (
                _session_row_datetime(row)
                or datetime.fromtimestamp(0, tz=timezone.utc)
            ).timestamp(),
            row.session_id or row.path,
        ),
        reverse=True,
    )


def _file_effective_time_key(
    path: Path, file_time_index: dict[str, datetime]
) -> tuple[float, str]:
    dt = file_time_index.get(str(path)) or _parse_session_path_datetime(str(path))
    if dt is not None:
        return (dt.timestamp(), str(path))
    try:
        return (path.stat().st_mtime, str(path))
    except OSError:
        return (0.0, str(path))


def _session_dedupe_key(row: SessionRow) -> str:
    # Session roots often contain both `.jsonl` and `.trajectory.jsonl` for the
    # same conversation.  Keep the newest parsed copy and do not waste session judge
    # budget on duplicate records.  Include a normalized first question as a
    # fallback for aggregate files where session_id is path-derived.
    sid = str(row.session_id or "").strip()
    query = re.sub(r"\s+", "", clean_query(row.first_question or row.user_text).lower())[:160]
    if sid:
        return f"sid:{sid}"
    return f"query:{query}|path:{Path(row.path).stem}"


def _session_file_scan_limit(max_sessions: int, time_window_enabled: bool = False) -> int:
    if time_window_enabled:
        # Time-scoped discovery must not spend the budget on files outside the
        # requested range.  Keep the candidate file window broad; parsed rows are
        # still capped by max_sessions after exact time filtering.
        return _default_int(100000)
    return max(100, max_sessions * 4)


def _recent_jsonl_files(
    root: Path,
    limit: int,
    time_window: _DiscoveryTimeWindow,
) -> tuple[list[Path], dict[str, datetime], dict[str, int]]:
    """Return newest JSONL candidates, optionally scoped to a time window.

    The scanner reads ``sessions.json`` as a lightweight index when available.
    If indexed or path-derived time is present, it is used for ordering and time
    pruning before parsing.  Otherwise file mtime is the fallback.  Parsed
    ``SessionRow`` time is still validated later, so this is an acquisition
    optimization rather than a correctness shortcut.
    """

    ignored_dirs = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        ".next",
        ".cache",
    }
    max_dirs = _default_int(20000)
    max_seen_files = _default_int(100000)
    metadata_index = _load_session_metadata_index(root)
    file_time_index: dict[str, datetime] = {}
    stats = {
        "metadata_entries": len(metadata_index),
        "dirs_seen": 0,
        "files_seen": 0,
        "files_index_time_pruned": 0,
        "files_path_time_pruned": 0,
    }

    def keyed(path: Path) -> tuple[float, str, Path] | None:
        dt = _metadata_datetime_for_file(path, metadata_index)
        source = "metadata" if dt is not None else ""
        if dt is None:
            dt = _parse_session_path_datetime(str(path))
            source = "path" if dt is not None else ""
        if dt is None:
            try:
                dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                source = "mtime"
            except OSError:
                return None

        # Only metadata/path-derived times are used for pre-parse pruning.  File
        # mtime can be changed by copy, compaction, or transcript post-processing,
        # so mtime is order-only and never excludes a file from a time-window run.
        if source in {"metadata", "path"}:
            in_window, _ = time_window.contains(dt)
            if not in_window:
                stats[f"files_{'index' if source == 'metadata' else 'path'}_time_pruned"] += 1
                return None
        file_time_index[str(path)] = dt
        return (dt.timestamp(), str(path), path)

    heap: list[tuple[float, str, Path]] = []
    try:
        walker = os.walk(root)
        for dirpath, dirnames, filenames in walker:
            stats["dirs_seen"] += 1
            if stats["dirs_seen"] > max_dirs:
                break
            dirnames[:] = [
                d for d in dirnames
                if d not in ignored_dirs and not d.startswith((".",))
            ]
            for filename in filenames:
                stats["files_seen"] += 1
                if stats["files_seen"] > max_seen_files:
                    break
                if not filename.endswith(".jsonl"):
                    continue
                item = keyed(Path(dirpath) / filename)
                if item is None:
                    continue
                if len(heap) < limit:
                    heapq.heappush(heap, item)
                elif item > heap[0]:
                    heapq.heapreplace(heap, item)
            if stats["files_seen"] > max_seen_files:
                break
    except OSError:
        return [], file_time_index, stats
    return [item[2] for item in sorted(heap, reverse=True)], file_time_index, stats


def _load_session_metadata_index(root: Path) -> dict[str, datetime]:
    """Build a best-effort timestamp index from OpenClaw ``sessions.json``."""

    store = root / "sessions.json"
    if not store.exists():
        return {}
    try:
        payload = json.loads(store.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}

    index: dict[str, datetime] = {}
    for record in _iter_session_metadata_records(payload):
        dt = _metadata_datetime(record)
        if dt is None:
            continue
        for key in _metadata_file_keys(record, root):
            index.setdefault(key, dt)
    return index


def _iter_session_metadata_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if any(
            k in value
            for k in ("sessionId", "session_id", "id", "path", "file", "updatedAt")
        ):
            records.append(value)
        for child in value.values():
            records.extend(_iter_session_metadata_records(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(_iter_session_metadata_records(child))
    return records


def _metadata_datetime(record: dict[str, Any]) -> datetime | None:
    for key in (
        "sessionStartedAt",
        "session_started_at",
        "startedAt",
        "started_at",
        "createdAt",
        "created_at",
        "startTime",
        "start_time",
        "timestamp",
        "time",
        # Interaction/update times are fallbacks only; they do not define the
        # user-visible session start used by sessions.json-first discovery.
        "lastInteractionAt",
        "last_interaction_at",
        "updatedAt",
        "updated_at",
    ):
        dt = _parse_session_datetime(str(record.get(key) or ""))
        if dt is not None:
            return dt
    return None


def _metadata_file_keys(record: dict[str, Any], root: Path) -> set[str]:
    keys: set[str] = set()
    for key in ("sessionId", "session_id", "id"):
        session_id = str(record.get(key) or "").strip()
        if session_id:
            keys.update(
                {session_id, f"{session_id}.jsonl", f"{session_id}.trajectory.jsonl"}
            )
    for path_value in _iter_path_like_values(record):
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            path = root / path
        keys.update({str(path), path.name, path.stem})
    return keys


def _iter_path_like_values(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and (
                key.lower()
                in {"path", "filepath", "file_path", "transcript", "transcriptpath"}
                or child.endswith((".jsonl", ".ndjson"))
            ):
                paths.append(child)
            elif isinstance(child, (dict, list)):
                paths.extend(_iter_path_like_values(child))
    elif isinstance(value, list):
        for child in value:
            paths.extend(_iter_path_like_values(child))
    return paths


def _metadata_datetime_for_file(
    path: Path, index: dict[str, datetime]
) -> datetime | None:
    for key in (str(path), path.name, path.stem):
        dt = index.get(key)
        if dt is not None:
            return dt
    return None
