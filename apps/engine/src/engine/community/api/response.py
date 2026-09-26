"""Shared HTTP response envelope used across all routers."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    """Canonical envelope returned by engine-adapter HTTP endpoints.

    `warning` is populated when the active engine services the capability with
    a documented limitation (see `EngineCapabilities.limited`). Callers should
    surface it so the user knows the response may be partial.
    """

    success: bool
    data: Optional[Any] = None
    message: Optional[str] = None
    warning: Optional[str] = None
    total: Optional[int] = None
    #: A machine-readable discriminator for a refusal, when `success` is False
    #: and the caller needs to branch on *which* refusal. `message` stays the
    #: human-readable text. Added for the CLI tools contract, whose "no such
    #: tool" answer must be distinguishable from "this engine build has no CLI
    #: endpoints" without reusing HTTP 404 for both
    #: (docs/bot-config-manifest/engine-requirements.zh-CN.md §4 A2).
    error: Optional[str] = None


__all__ = ["ApiResponse"]
