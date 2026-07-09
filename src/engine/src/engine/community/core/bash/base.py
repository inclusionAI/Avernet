from __future__ import annotations

import asyncio
import logging
import os

from engine.community.core.bash.models import BashExecResult
from engine.community.core.bash.protocol import BashService
from engine.community.core.engine.context import AuthContext

log = logging.getLogger("bash")

# Working directory whitelist prefixes
_RELAY_CWD = os.getenv("RELAY_DEFAULT_CWD")
_EXTRA = tuple(
    p.strip().rstrip("/") + "/"
    for p in os.getenv("BASH_ALLOWED_CWD_PREFIXES", "").split(",")
    if p.strip()
)
ALLOWED_CWD_PREFIXES = (
    ("/home/admin/", "/workspace/")
    + ((_RELAY_CWD.rstrip("/") + "/",) if _RELAY_CWD else ())
    + _EXTRA
)

# Maximum timeout in seconds
MAX_TIMEOUT = 120


class BaseBashService(BashService):
    """Generic bash execution service using asyncio.subprocess.

    All engine types share this implementation since bash execution
    is container-local and engine-agnostic.
    """

    async def exec(
        self,
        cmd: str,
        cwd: str,
        timeout: int = 30,
        auth: AuthContext | None = None,
    ) -> BashExecResult:
        """Execute a bash command in the given working directory.

        Security constraints:
        - cwd must be under an allowed prefix (/home/admin/ or /workspace/)
        - timeout is capped at MAX_TIMEOUT (120s)
        - Command runs in a subprocess; SIGKILL on timeout
        """
        if not any(cwd.startswith(prefix) for prefix in ALLOWED_CWD_PREFIXES):
            raise ValueError(f"cwd not allowed: {cwd}")

        timeout = min(timeout, MAX_TIMEOUT)

        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return BashExecResult(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return BashExecResult(
                stdout="",
                stderr=f"command timed out after {timeout}s",
                exit_code=-1,
            )


__all__ = ["BaseBashService", "ALLOWED_CWD_PREFIXES", "MAX_TIMEOUT"]
