from __future__ import annotations

from pydantic import BaseModel, Field


class BashExecRequest(BaseModel):
    cmd: str = Field(..., description="Command to execute")
    cwd: str = Field(..., description="Working directory (must be within allowed prefixes)")
    timeout: int = Field(30, description="Timeout in seconds (max 120)")


class BashExecResponse(BaseModel):
    stdout: str = Field(..., description="Standard output")
    stderr: str = Field(..., description="Standard error")
    exit_code: int = Field(..., description="Exit code (0=success, -1=timeout)")


__all__ = ["BashExecRequest", "BashExecResponse"]
