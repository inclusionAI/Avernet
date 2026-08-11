"""Default-capability engine-bucket routing helpers.

The common MCP/CLI defaults layer is engine-agnostic: it normalizes public
engine spelling, then delegates bucket overrides to the engine registry. Engine
modules contribute their own resolver implementations from that registry rather
than branching here on engine-name literals.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_default_capabilities_engine_bucket,
)


def normalize_raw_engine_type(
    engine_type: Any,
    *,
    default: str = DEFAULT_ENGINE_TYPE,
) -> str:
    if engine_type is None:
        return default.strip().lower().replace("-", "_")
    if not isinstance(engine_type, str):
        return ""

    stripped = engine_type.strip()
    if not stripped:
        stripped = default
    return stripped.strip().lower().replace("-", "_")


def normalize_template_type(template_type: Any) -> str:
    if not isinstance(template_type, str):
        return ""
    return template_type.strip().lower()


def normalize_engine_type(
    engine_type: Any,
    template_type: Any = None,
    *,
    default: str = DEFAULT_ENGINE_TYPE,
) -> str:
    """Normalize to the engine bucket used by default MCP/CLI capabilities."""
    normalized_engine = normalize_raw_engine_type(engine_type, default=default)
    normalized_template_type = normalize_template_type(template_type) or None
    return resolve_default_capabilities_engine_bucket(
        engine_type=normalized_engine,
        template_type=normalized_template_type,
    )


def resolve_default_capabilities_engine_type(
    engine_type: Any,
    template_type: Any = None,
) -> str:
    """Resolve the engine bucket used by default MCP/CLI capability lists."""
    return normalize_engine_type(engine_type, template_type)
