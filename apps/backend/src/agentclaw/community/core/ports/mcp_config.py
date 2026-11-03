"""One write-intent contract, independent of the configuration value's shape.

A value replaces the override; None clears it; PreserveMcpConfig leaves it
untouched. Compilers use typed McpOverride values; persistence ports use dicts.
These are in-process instructions, never serialized to YAML or Genome.
"""

from dataclasses import dataclass
from typing import TypeAlias, TypeVar

__all__ = ["PreserveMcpConfig", "McpConfigWriteIntent"]


@dataclass(frozen=True, slots=True)
class PreserveMcpConfig:
    """No configuration write, including when an intent is copied or restored."""


_Config = TypeVar("_Config")
McpConfigWriteIntent: TypeAlias = _Config | None | PreserveMcpConfig
