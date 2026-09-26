"""Default-config plugin data models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DefaultConfigResult:
    """Outcome of :meth:`DefaultConfigService.get_default_config`.

    `path` is the resolved on-disk location of the config (frontend
    surfaces it for debugging); `config` is the parsed JSON.
    """

    path: str
    config: dict[str, Any]


__all__ = ["DefaultConfigResult"]
