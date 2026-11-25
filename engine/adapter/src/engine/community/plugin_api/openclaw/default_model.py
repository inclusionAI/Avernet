"""Optional Plugin API for reading the OpenClaw configuration.

The default connection calls existing config.get; no caller token or file path
is accepted. Return the config.get response, including exists/valid/parsed.
Failures raise.
The response can contain secrets: never log or publish it. Core extracts only
agents.defaults.model, without the runtime defaults added to response.config.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OpenClawDefaultModelPort(Protocol):
    async def get_model_config(self) -> dict[str, Any]:
        """Read config.get on the already-addressed engine connection."""
        ...
