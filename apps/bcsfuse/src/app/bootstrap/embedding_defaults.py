"""Resolve the public embedding model contract at composition time only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_EMBEDDING_MODEL = "Qwen3-Embedding-4B"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_EMBEDDING_REQUEST_DIMENSIONS = True


@dataclass(frozen=True, slots=True)
class EmbeddingRuntimeDefaults:
    model: str
    dimension: int
    request_dimensions: bool


def resolve_embedding_runtime_defaults(
    config: Mapping[str, Any],
    env: Mapping[str, str],
) -> EmbeddingRuntimeDefaults:
    """Resolve deployment settings in one place with explicit efficient defaults.

    Environment variables retain deployment precedence. If a caller selects a
    non-native dimension, it must explicitly disable the OpenAI dimensions
    request on a service known not to support it; the embedding settings
    validator then rejects that unsafe composition.
    """

    model = _first_value(
        _string_env(env, "EMBEDDING_MODEL"),
        _config_value(config, "embedding.model"),
        DEFAULT_EMBEDDING_MODEL,
    )
    dimension = int(
        _first_value(
            _string_env(env, "EMBEDDING_DIMENSION"),
            _config_value(config, "embedding.dimension"),
            DEFAULT_EMBEDDING_DIMENSION,
        )
    )

    raw_request_dimensions = _first_value(
        _string_env(env, "EMBEDDING_REQUEST_DIMENSIONS"),
        _config_value(config, "embedding.request_dimensions"),
        DEFAULT_EMBEDDING_REQUEST_DIMENSIONS,
    )
    request_dimensions = _as_bool(raw_request_dimensions)

    return EmbeddingRuntimeDefaults(
        model=model,
        dimension=dimension,
        request_dimensions=request_dimensions,
    )


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    raise ValueError("embedding setting must resolve to a non-empty value")


def _string_env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _config_value(config: Mapping[str, Any], path: str) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        value = getter(path)
        if value is not None:
            return value
    current: Any = config
    for key in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("embedding.request_dimensions must be a boolean")


__all__ = [
    "DEFAULT_EMBEDDING_DIMENSION",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_REQUEST_DIMENSIONS",
    "EmbeddingRuntimeDefaults",
    "resolve_embedding_runtime_defaults",
]
