"""Neutral values for current-runtime file counting."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FileCountQuery:
    bot_id: str
    entity_id: str
    stage: Literal["draft", "verify", "online"]
    path: str
    request_id: str


@dataclass(frozen=True)
class FileCountBinding:
    id: int
    device_provider: str
    device_id: str


class FileCountError(ValueError):
    """A stable safe error code, without upstream exception details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def safe_log_fields(value):
    """Copy structured boundary fields and recursively remove reusable credentials."""
    # COSEC: case-insensitive credential matching also covers nested lists/maps.
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(part in str(key).lower() for part in (
                "token", "authorization", "cookie", "password", "secret", "key", "credential", "session",
            )) else safe_log_fields(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_log_fields(item) for item in value]
    return value
