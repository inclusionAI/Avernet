"""Additive error fields for engines using the OpenClaw chat wire format."""
from typing import Any

from engine.community.kernel.frames import ErrorCodes


def complete_chat_error(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve existing messages/extensions and never mutate a shared event."""
    result = dict(payload)
    code = result.get("errorCode")
    result["errorCode"] = (
        str(code) if isinstance(code, (str, int)) and str(code).strip()
        else ErrorCodes.UNAVAILABLE
    )
    message = result.get("errorMessage")
    if not isinstance(message, str) or not message.strip():
        result["errorMessage"] = "Unknown error"
    return result
