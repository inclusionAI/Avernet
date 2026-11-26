"""Bounded, redacted previews of the decoded business request at search entry."""

import re
from contextvars import ContextVar

search_request_body: ContextVar[dict[str, object] | None] = ContextVar(
    "search_request_body", default=None
)

search_caller_app: ContextVar[str] = ContextVar("search_caller_app", default="")

# Keep request logs readable without copying whole prompts or common identifiers.
_sensitive_text = re.compile(
    r"(?i)\bbearer\s+[a-z0-9._~+/=-]+|(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)"
)


def log_text(value):
    return _sensitive_text.sub("[redacted]", value)[:256]


_sensitive_field_pattern = re.compile(
    r"password|passwd|secret|token|authorization|cookie|api.?key|private.?key|credential",
    re.IGNORECASE,
)


def received_body_fields(allowed_filters):
    body = search_request_body.get()
    if body is None:
        return {}

    nodes_left = 96
    chars_left = 2048
    truncated = False

    def text(value):
        nonlocal chars_left, truncated
        redacted = _sensitive_text.sub("[redacted]", value)
        limit = min(256, chars_left)
        truncated |= len(redacted) > limit
        result = redacted[:limit]
        chars_left -= len(result)
        return result

    def preview(item, *, hide=False, filters=False, filter_entry=False):
        nonlocal nodes_left, truncated
        nodes_left -= 1
        if hide:
            return "[redacted]"
        if isinstance(item, str):
            return text(item)
        if isinstance(item, list):
            result = []
            for value in item:
                if not nodes_left:
                    truncated = True
                    break
                result.append(preview(value, filter_entry=filters))
            return result
        if isinstance(item, dict):
            fields = {}
            hide_values = filter_entry and item.get("key") not in allowed_filters
            for key, value in item.items():
                if not nodes_left or not chars_left:
                    truncated = True
                    break
                logged_key = text(key)
                if logged_key in fields:
                    truncated = True
                    continue
                fields[logged_key] = preview(
                    value,
                    hide=bool(_sensitive_field_pattern.search(key))
                    or (key == "values" and hide_values),
                    filters=item is body and key == "filters",
                )
            return fields
        return item

    context = body.get("context") or {}
    caller_app = search_caller_app.get()
    result = {
        **({"caller_app": log_text(caller_app)} if caller_app else {}),
        "has_goal": context.get("goal") is not None or body.get("goal") is not None,
        "has_task": context.get("task") is not None or body.get("task") is not None,
        "request_body": preview(body),
        "request_body_truncated": truncated,
    }
    return result
