from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BashExecResult:
    """Result of a bash command execution."""

    stdout: str
    stderr: str
    exit_code: int


__all__ = ["BashExecResult"]
