"""Resolve the shared embedding token pool at composition time only.

A deployment injects the same token pool into every Pod. The application never
persists the plaintext credentials; only a short, non-reversible lane key is
used for metrics, logs, and global capacity keys.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

_TOKEN_POOL_ENV = "EMBEDDING_AUTH_TOKENS"
_SINGLE_TOKEN_ENV = "EMBEDDING_AUTH_TOKEN"


def resolve_embedding_token_pool(
    config: Any,
    env: Mapping[str, str],
    *,
    require_at_least_one: bool = False,
) -> list[str]:
    """Return the deployment-wide, ordered, non-empty token credentials.

    ``EMBEDDING_AUTH_TOKENS`` accepts JSON array injection (preferred for a
    large, dynamic pool) or a comma-separated list. ``EMBEDDING_AUTH_TOKEN``
    remains fully compatible and describes a one-token pool. Empty entries are
    removed and exact duplicates are collapsed without changing order.
    """

    raw_pool = env.get(_TOKEN_POOL_ENV)
    if raw_pool is not None:
        return _parse_pool(raw_pool)

    config_pool = _config_value(config, "embedding.auth_tokens")
    if config_pool is not None:
        return _parse_pool(config_pool)

    raw_single = _string_or_none(env.get(_SINGLE_TOKEN_ENV))
    if raw_single is None:
        raw_single = _string_or_none(_config_value(config, "embedding.auth_token"))
    tokens = _normalize_entries([raw_single])

    if require_at_least_one and not tokens:
        raise ValueError("embedding token pool must contain one non-empty token")

    return tokens


def token_lane_key(token: str) -> str:
    """Return a stable, non-secret lane key for one token credential.

    All Pods compute the same key from the same credential, which lets the
    shared capacity store coordinate the same token across replicas.
    """

    if not token:
        raise ValueError("token lane requires a non-empty credential")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return digest[:16]


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("embedding auth token must be a string")
    normalized = value.strip()
    return normalized or None


def _config_value(config: Any, path: str) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(path)

    current: Any = config
    for key in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _parse_pool(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        raise ValueError("embedding.auth_tokens must be a list, JSON array, or CSV")
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise ValueError("EMBEDDING_AUTH_TOKENS must be valid JSON") from error
            if not isinstance(decoded, list):
                raise ValueError("EMBEDDING_AUTH_TOKENS JSON must be an array")
            return _normalize_entries(decoded)
        return _normalize_entries(stripped.split(","))

    if isinstance(raw, Sequence):
        return _normalize_entries(raw)
    raise ValueError("embedding.auth_tokens has an unsupported format")


def _normalize_entries(values: Sequence[Any]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError("embedding tokens must be strings")
        token = value.strip()
        if token:
            tokens.append(token)
    return list(dict.fromkeys(tokens))


__all__ = [
    "resolve_embedding_token_pool",
    "token_lane_key",
]
