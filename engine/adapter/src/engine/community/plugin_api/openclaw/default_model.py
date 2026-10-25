"""Optional Plugin API for reading the live OpenClaw config snapshot.

The default connection calls existing config.get; no caller token or file path
is accepted. Return the snapshot, including exists/valid/parsed. Failures raise.
The snapshot can contain secrets: never log or publish it. Core extracts only
agents.defaults.model, without the runtime defaults added to snapshot.config.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OpenClawDefaultModelPort(Protocol):
    async def model_config_snapshot(self) -> dict[str, Any]:
        """Read config.get on the already-addressed engine connection."""
        ...
