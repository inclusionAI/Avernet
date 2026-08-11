"""Default-capability engine-bucket routing."""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

_AICODING_ENGINE_TYPE = "aicoding"
_CLAUDE_CODE_ENGINE_TYPE = "claude_code"
_NORMAL_CC_TEMPLATE_TYPE = "normalcc"


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
    """Normalize to the engine bucket used by default MCP/CLI capabilities.

    The bucket policy is intentionally the same one exposed by
    :func:`resolve_default_capabilities_engine_type`, and follows the BaaS
    routing rule:
    * explicit ``aicoding`` uses the AICoding bucket;
    * ``claude_code`` with a non-empty template type other than ``normalCC``
      reuses the AICoding bucket;
    * otherwise use the normalized engine type.
    """
    normalized_engine = normalize_raw_engine_type(engine_type, default=default)
    if normalized_engine == _AICODING_ENGINE_TYPE:
        return _AICODING_ENGINE_TYPE

    if normalized_engine == _CLAUDE_CODE_ENGINE_TYPE:
        normalized_template_type = normalize_template_type(template_type)
        if (
            normalized_template_type
            and normalized_template_type != _NORMAL_CC_TEMPLATE_TYPE
        ):
            return _AICODING_ENGINE_TYPE

    return normalized_engine


def resolve_default_capabilities_engine_type(
    engine_type: Any,
    template_type: Any = None,
) -> str:
    """Resolve the engine bucket used by default MCP/CLI capability lists."""
    return normalize_engine_type(engine_type, template_type)
