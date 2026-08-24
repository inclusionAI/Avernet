"""Value objects for an Engine-mediated Chat file share."""

from __future__ import annotations

from dataclasses import dataclass


class ChatFileShareError(RuntimeError):
    """A stable, non-sensitive error visible to the local CLI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ChatFileShareResult:
    file_name: str
    size_bytes: int
    share_url: str
    expires_at: str
