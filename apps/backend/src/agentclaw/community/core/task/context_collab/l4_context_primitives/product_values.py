"""Strict shared input bounds. Never include bodies in validation errors."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)

MAX_CONTENT_BYTES = 256 * 1024


def positive_id(value: int) -> None:
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise Invalid("identity/revision must be a positive signed 64-bit integer")


def text(value: str, *, empty: bool = False, maximum: int = MAX_CONTENT_BYTES) -> None:
    if type(value) is not str or len(value.encode("utf-8")) > maximum:
        raise Invalid("text has invalid type or exceeds byte limit")
    if not empty and not value.strip():
        raise Invalid("text must not be blank")


def shape(data: dict, names: set[str]) -> None:
    if type(data) is not dict or set(data) != names:
        raise Invalid("unknown or missing value fields")
