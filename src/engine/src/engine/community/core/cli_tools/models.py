"""Data models for CLI tool delivery.

One declared tool is **one command backed by one executable file**. The
platform has already unpacked archives, selected the single declared member,
enforced the user-pinned ``sha256`` and validated the ELF header by the time
anything here sees bytes, so these models carry no source, no archive shape and
no pin — just a command name and the file behind it.

Contract: ``src/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md``
§4 A2.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CliToolInfo:
    """One command as it exists **on disk right now**.

    Returned by listing. ``md5`` is computed from the bytes actually read, not
    from anything remembered about the last write — that is what lets a caller
    notice a same-named binary that was swapped underneath it.
    """

    name: str
    md5: str
    size_bytes: int


@dataclass(frozen=True)
class CliToolPayload:
    """One command to place: its name and the executable's bytes."""

    name: str
    data: bytes


@dataclass(frozen=True)
class CliToolBytes:
    """One command read back off disk, bytes included."""

    name: str
    data: bytes
    md5: str

    @property
    def size_bytes(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class CliToolResult:
    """The verdict for **one** name in a whole-set replacement.

    Every requested name gets one of these, successes and failures alike. The
    platform parses the set strictly and treats a missing name as an unreadable
    response rather than an implicit success (§4 A2), because silence read as
    success is exactly how a tool the bot does not have ends up in a green
    apply report.
    """

    name: str
    success: bool
    message: str | None = None


__all__ = [
    "CliToolBytes",
    "CliToolInfo",
    "CliToolPayload",
    "CliToolResult",
]
