"""OpenClaw default_config ACL adapter.

Implements the core ``DefaultConfigService`` by delegating to an injected
``OpenClawDefaultConfigPort`` and translating the port's primitive dict into
a ``DefaultConfigResult`` DTO.  The file read, path resolution, and JSON
parsing live in the port impl (leaf side).

Exceptions raised by the port (FileNotFoundError, IsADirectoryError,
ValueError) propagate unchanged — the router maps them to HTTP status codes
as documented in core/default_config/protocol.py.
"""
from __future__ import annotations

from engine.community.core.default_config.models import DefaultConfigResult
from engine.community.core.default_config.protocol import DefaultConfigService
from engine.community.core.engine.context import AuthContext
from engine.community.plugin_api.openclaw.default_config import OpenClawDefaultConfigPort


class OpenClawDefaultConfigAdapter(DefaultConfigService):
    """`DefaultConfigService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawDefaultConfigPort) -> None:
        self._port = port

    async def get_default_config(
        self, auth: AuthContext | None = None,
    ) -> DefaultConfigResult:
        raw = await self._port.get_default_config()
        return DefaultConfigResult(
            path=raw["path"],
            config=raw["config"],
        )


__all__ = ["OpenClawDefaultConfigAdapter"]
