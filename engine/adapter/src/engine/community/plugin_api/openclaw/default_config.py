"""OpenClawDefaultConfigPort — native port for default-config access.

Default-config is local-infra: it reads a JSON file from the pod filesystem —
no gateway, no pool, no token.  The path resolution (env var lookup +
``OPENCLAW_DEFAULT_CONFIG_PATH`` default) and the JSON parse live in the port
impl; this port returns a primitive dict so the adapter can build a
``DefaultConfigResult`` DTO without touching the filesystem.

Exceptions raised by the impl propagate unchanged:
  - ``FileNotFoundError`` — config file absent
  - ``IsADirectoryError`` — resolved path is a directory
  - ``ValueError``  — JSON parse failure or non-object top-level
"""
from __future__ import annotations

from typing import Any, Protocol


class OpenClawDefaultConfigPort(Protocol):
    """Native default-config access over the OpenClaw daas-scripts JSON file."""

    async def get_default_config(self) -> dict[str, Any]:
        """Read and parse the OpenClaw default config JSON file.

        Returns a primitive dict with keys:
          ``path`` (str) — resolved on-disk location of the config file,
          ``config`` (dict) — the parsed JSON object.
        Raises ``FileNotFoundError`` when the file does not exist,
        ``IsADirectoryError`` when the resolved path is a directory,
        ``ValueError`` on JSON parse failure or a non-object top level.
        """
        ...


__all__ = ["OpenClawDefaultConfigPort"]
