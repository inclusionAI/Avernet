"""Helpers for extracting session IDs at the HTTP response boundary."""

from collections.abc import Mapping
from typing import Any


def extract_response_session_id(
    metadata: Mapping[str, Any] | None,
    result_extra: Mapping[str, Any] | None,
) -> str:
    """Return the first string session ID from the response sources.

    Metadata keeps precedence for compatibility with the existing response
    behavior. Invalid values are ignored so they cannot violate the response
    model's required string contract.
    """
    for source in (metadata, result_extra):
        if source is None:
            continue
        session_id = source.get("session_id")
        if isinstance(session_id, str):
            return session_id
    return ""
