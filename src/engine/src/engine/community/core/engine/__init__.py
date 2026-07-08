"""
Engine framework — abstractions for heterogeneous engine support.

This package defines the contracts every engine must satisfy:
  - protocol.Engine: the structural interface
  - capability.Capability / EngineCapabilities: declarative capability system
  - base.BaseEngine: optional ABC providing default plumbing
  - registry.EngineRegistry: name → engine class lookup
  - exceptions: shared error types
  - health.HealthStatus: result type for Engine.health_check()

Engine implementations live under engines/<name>/ and depend on this package.
Web layer and EngineManager talk to engines through these contracts.
"""
from __future__ import annotations

from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.exceptions import (
    CapabilityNotSupportedError,
    EngineError,
    EngineNotFoundError,
)
from engine.community.core.engine.health import HealthStatus
from engine.community.core.engine.naming import known_canonicals, normalize
from engine.community.core.engine.protocol import Engine
from engine.community.core.engine.registry import EngineRegistry

__all__ = [
    "BaseEngine",
    "Capability",
    "CapabilityNotSupportedError",
    "Engine",
    "EngineCapabilities",
    "EngineError",
    "EngineNotFoundError",
    "EngineRegistry",
    "HealthStatus",
    "known_canonicals",
    "normalize",
]
