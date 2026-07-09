from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.bash.models import BashExecResult
from engine.community.core.engine.context import AuthContext


@runtime_checkable
class BashService(Protocol):
    """Protocol for executing bash commands inside a container/workspace."""

    async def exec(
        self,
        cmd: str,
        cwd: str,
        timeout: int = 30,
        auth: AuthContext | None = None,
    ) -> BashExecResult:
        """Execute a bash command in the given working directory.

        Args:
            cmd: The shell command to execute.
            cwd: Working directory (must be within allowed prefixes).
            timeout: Maximum execution time in seconds (capped at MAX_TIMEOUT).
            auth: Optional auth context.

        Returns:
            BashExecResult with stdout, stderr, and exit_code.
        """
        ...


__all__ = ["BashService"]
