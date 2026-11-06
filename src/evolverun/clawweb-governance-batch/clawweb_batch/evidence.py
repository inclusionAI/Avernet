"""Bounded raw-evidence extraction. Log content is data, never executable instructions."""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from .core import compact_hash, instant

SENSITIVE_FIELD_PATTERN = re.compile(r"(?i)(api[-_]?key|token|cookie|authorization|password|secret|credential|access[-_]?key)")
ERROR_CODE = re.compile(r"\b(?:ODPS-\d+|SYSTEM_ERROR|AccessDenied|PermissionDenied|EACCES|ENOENT|ETIMEDOUT)\b|\b(?:401|403|429|502|503|504)\s+(?:Forbidden|Unauthorized|Too Many Requests|Bad Gateway|Service Unavailable|Gateway Timeout)", re.I)


def redact(value: str) -> str:
    text = str(value)
    text = re.sub(r"(?im)^.*(?:authorization|cookie|api[_-]?key|access[_-]?key|token|password|secret)\s*[\"':=].*$",
                  "[credential-bearing line omitted]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"https?://[^\s\"'<>]+", "[URL]", text)
    text = re.sub(r"\b[A-Za-z0-9_+/=-]{40,}\b", "[LONG_VALUE]", text)
    text = re.sub(r"\b\d{11,}\b", "[LONG_ID]", text)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", text)
    return text


def text_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x.get("text", "")) for x in value if isinstance(x, dict)
                         and x.get("type", "text") in {"text", "output_text"})
    return ""


def source_scope(path: str) -> str:
    if not path or "/sessions/" not in path:
        return "unknown"
    # Preserve source, engine, device and agent scope. Never equate a desktop with a NAS bot.
    return path.split("/sessions/", 1)[0]


def operation_name(tool: str, arguments: dict) -> str:
    parts = [tool]
    command = str(arguments.get("command", ""))
    if command:
        # Metadata only; never execute or trust commands extracted from another Agent's logs.
        scripts = re.findall(r"(?:^|\s)([\w./~-]+\.(?:py|sh))\b", command)
        tables = re.findall(r"(?i)\b(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)", command)
        calls = re.findall(r"\b(?:mcporter\s+call|dataphin)\s+([\w.-]+(?:\s+[\w.-]+)?)", command)
        parts += [PurePosixPath(p).name for p in scripts[:2]] + sorted(set(tables))[:4] + calls[:2]
        if len(parts) == 1:
            first = re.match(r"\s*([\w./~-]+)", command)
            if first:
                parts.append(PurePosixPath(first.group(1)).name)
    else:
        for key in ("action", "operation", "method", "tool"):
            value = arguments.get(key)
            if isinstance(value, str) and re.fullmatch(r"[\w.-]{1,80}", value):
                parts.append(value)
    return ":".join(parts)


def error_codes(text: str, is_error: bool) -> list[str]:
    found = {m.group(0).upper() for m in ERROR_CODE.finditer(text)}
    for m in re.finditer(r"(?i)\b(?:HTTP(?:\s+Error)?\s*)?([45]\d\d)[:\s]+(?:Forbidden|Unauthorized|Not Found|Too Many Requests|Bad Gateway|Service Unavailable|Gateway Timeout)\b", text):
        found.add("HTTP_" + m.group(1))
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    def visit(value: object, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"errorcode", "error_code"} and isinstance(item, (str, int)):
                    code = str(item).strip()
                    if code.upper() not in {"", "0", "200", "SUCCESS", "OK", "NONE", "NULL"} and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", code):
                        found.add(code.upper())
                if key.lower() in {"statuscode", "status_code", "http_status"} and isinstance(item, int) and 400 <= item <= 599:
                    found.add("HTTP_" + str(item))
                if key.lower() == "success" and item is False:
                    found.add("BUSINESS_SUCCESS_FALSE")
                if key.lower() == "status" and isinstance(item, str) and item.lower() in {"error", "failed", "failure"}:
                    found.add("BUSINESS_STATUS_ERROR")
                visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value[:100]:
                visit(item, depth + 1)
    visit(parsed)
    if is_error:
        found.add("TOOL_ERROR")
    return sorted(found)


def extract_tasks(rows: list[dict], max_bytes: int) -> tuple[list[dict], list[str]]:
    tasks, warnings, seen = [], [], set()
    for row in rows:
        identity = (row["user_id"], row["bot_id"], row["session_id"])
        if identity in seen:
            warnings.append("duplicate session row")
            continue
        seen.add(identity)
        if int(row.get("source_rows") or 1) > 1:
            warnings.append(f"{row['session_id']}: ambiguous source snapshots")
            continue
        raw = row.get("messages") or ""
        if not isinstance(raw, str) or len(raw.encode()) > max_bytes:
            warnings.append(f"{row['session_id']}: missing/oversized messages")
            continue
        try:
            messages, definitions = json.loads(raw), json.loads(row.get("llm_tasks_json") or "[]")
            if not isinstance(messages, list) or not isinstance(definitions, list) or not definitions:
                raise ValueError("missing task boundaries")
            if any(not isinstance(x, dict) for x in messages):
                raise ValueError("invalid message list")
            messages = [x.get("message", x) for x in messages]
            if any(not isinstance(x, dict) for x in messages):
                raise ValueError("invalid wrapped message")
            indices = set()
            for position, definition in enumerate(definitions):
                index = definition.get("task_index", position)
                bounds = definition.get("message_range")
                if isinstance(bounds, str):
                    bounds = json.loads(bounds)
                if type(index) is not int or index < 0 or index in indices:
                    raise ValueError("duplicate/invalid task index")
                indices.add(index)
                if (not isinstance(bounds, list) or len(bounds) != 2 or
                        any(type(v) is not int for v in bounds) or not 0 <= bounds[0] < bounds[1] <= len(messages)):
                    raise ValueError("invalid message_range")
                tasks.append(_task(row, definition, index, messages, bounds))
        except (ValueError, TypeError, KeyError, AttributeError):
            warnings.append(f"{row['session_id']}: invalid task/evidence shape")
    return tasks, warnings


def _task(row: dict, definition: dict, index: int, messages: list[dict], bounds: list[int]) -> dict:
    source = source_scope(str(row.get("file_path") or ""))
    calls, evidence, signatures, operations, files = {}, [], {}, set(), set()
    adjacent_calls, adjacent_index = [], -2
    cron = ""
    for m in messages[:3]:
        match = re.search(r"\[cron:([a-fA-F0-9-]{36})\b", text_content(m.get("content")))
        if match:
            cron = match.group(1).lower()
            break
    for i, msg in enumerate(messages[:bounds[1]]):
        content = msg.get("content", [])
        current_calls = []
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or msg.get("role") != "assistant":
                continue
            typed = item.get("type") in {"toolCall", "tool_use"}
            normalized = "type" not in item and "name" in item and "arguments" in item
            if not (typed or normalized):
                continue
            args = item.get("arguments", item.get("input", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            name = str(item.get("name", ""))
            if item.get("id"):
                calls[item["id"]] = (name, args)
            if normalized:
                current_calls.append((name, args))
            if bounds[0] <= i and name == "read":
                path = args.get("file_path", args.get("path"))
                if isinstance(path, str) and path.endswith(("SKILL.md", "TOOLS.md", "AGENTS.md")):
                    files.add(path)
        if msg.get("role") == "assistant":
            adjacent_calls, adjacent_index = current_calls, i
        if i < bounds[0]:
            continue
        role = msg.get("role")
        text = text_content(content)
        if role in {"user", "assistant"} and text:
            evidence.append({"message_index": i, "role": role, "text": redact(text)[:1800]})
        if role not in {"toolResult", "tool"}:
            continue
        call_id = msg.get("toolCallId", msg.get("tool_call_id", msg.get("tool_use_id")))
        matched = calls.get(call_id) if call_id else None
        pairing = "call_id"
        if (not call_id and len(adjacent_calls) == 1 and i == adjacent_index + 1
                and adjacent_calls[0][0] == msg.get("toolName")):
            matched = adjacent_calls[0]
            pairing = "single_adjacent_normalized_call"
        tool, args = matched if matched else (str(msg.get("toolName", "unknown")), {})
        if tool in {"read", "Read", "read_file"}:
            continue  # A code example or error mentioned in SKILL.md is not a runtime error.
        op = operation_name(tool, args)
        operation_id = compact_hash([source, cron, op])
        operations.add(operation_id)
        codes = error_codes(text, msg.get("isError") is True)
        item = {"message_index": i, "role": "toolResult", "tool": tool, "operation": op,
                "operation_id": operation_id, "pairing": pairing if matched else "unpaired", "error_codes": codes, "text": redact(text)[:2200]}
        evidence.append(item)
        if source != "unknown" and matched:
            for code in codes:
                signature = {"source": source, "cron_job": cron, "component": op,
                             "error_code": code, "operation_id": operation_id}
                signatures[compact_hash(signature)] = signature
    end_time = instant(str(row["end_time"])).isoformat()
    state = definition.get("is_complete", 2)
    state = int(state) if isinstance(state, (int, bool)) or str(state).isdigit() else 2
    return {"id": f"{row['session_id']}:{index}", "session_id": row["session_id"], "task_index": index,
            "user_id": row["user_id"], "bot_id": row["bot_id"], "dt": row["dt"],
            "end_time": end_time, "start_time": instant(str(row["start_time"])).isoformat(), "source": source, "is_cron": bool(row.get("sampling_group_key")),
            "is_complete": state, "failure_class": str(definition.get("task_failure_class", "UNKNOWN")),
            "description": redact(str(definition.get("task_description", "")))[:1200],
            "judge_reason": redact(str(definition.get("reasoning", "")))[:1500],
            "signature_ids": sorted(signatures), "signatures": signatures,
            "operation_ids": sorted(operations), "config_paths": sorted(files), "evidence": evidence}
