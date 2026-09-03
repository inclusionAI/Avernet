"""Convert the human-chat operation inventory into policy-table rows."""

from collections.abc import Callable
from typing import Any

BASE = "/openapi/v1/bots/{bot_id}/human-chat/sessions"
OPERATIONS = (
    ("GET", ""),
    ("POST", ""),
    ("GET", "/favorites"),
    ("GET", "/{session_id}"),
    ("PATCH", "/{session_id}"),
    ("DELETE", "/{session_id}"),
    ("GET", "/{session_id}/connection"),
    ("GET", "/{session_id}/messages"),
    ("DELETE", "/{session_id}/messages"),
    ("PUT", "/{session_id}/favorite"),
    ("DELETE", "/{session_id}/favorite"),
)


def authorization_rows(no_check: Callable[[str], Any]) -> dict[tuple[str, str], Any]:
    reason = "BCN friendship and caller-owned session are checked by human_chat"
    return {(method, BASE + suffix): no_check(reason) for method, suffix in OPERATIONS}


__all__ = ["BASE", "OPERATIONS", "authorization_rows"]
