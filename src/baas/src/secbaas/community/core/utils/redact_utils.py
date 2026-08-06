"""Recursive redaction for credential-bearing fields in logs and API responses.

thetaKey ciphertext (``extra_properties`` envelope) and other credential fields
must never reach response DTOs or service logs. This module provides the single
recursive sanitizer used at every egress: response construction and publish
config logging. It only produces copies — runtime data is untouched.
"""

from __future__ import annotations

from typing import Any

# Normalized (strip separators, lowercase) key names whose values are redacted.
SENSITIVE_LOG_KEYS = frozenset(
    {
        "extraproperties",
        "apikey",
        "authorization",
        "token",
    }
)

_REDACTED = "<redacted>"


def _normalize_key(key: Any) -> str:
    return str(key).replace("_", "").replace("-", "").lower()


def redact_sensitive(value: Any) -> Any:
    """Return a recursively redacted copy of ``value``.

    Dict keys matching :data:`SENSITIVE_LOG_KEYS` (after normalization) have
    their values replaced with ``"<redacted>"``; nested dicts/lists/tuples are
    walked. Scalars are returned as-is. The input is never mutated.
    """
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            redacted[key] = (
                _REDACTED
                if _normalize_key(key) in SENSITIVE_LOG_KEYS
                else redact_sensitive(item)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    return value


def log_safe_model(value: Any) -> Any:
    """Convert a pydantic model / object / dict to a log-safe redacted structure.

    Used for logging publish/bot configs so f-string dumps never carry
    ciphertext. Returns a plain structure; runtime objects are untouched.
    """
    try:
        if hasattr(value, "model_dump"):
            raw = value.model_dump(exclude_none=True)
        elif isinstance(value, dict):
            raw = value
        elif hasattr(value, "__dict__"):
            raw = vars(value)
        else:
            raw = _safe_repr(value)
        return redact_sensitive(raw)
    except Exception as exc:
        return {
            "value_type": type(value).__name__,
            "dump_error_type": type(exc).__name__,
        }


def _safe_repr(obj: object, max_len: int = 4096) -> str:
    try:
        s = repr(obj)
        if len(s) > max_len:
            s = s[: max_len - 3] + "..."
        return s
    except Exception as e:  # noqa: BLE001
        return f"<repr failed: {e}>"


__all__ = ["SENSITIVE_LOG_KEYS", "redact_sensitive", "log_safe_model"]
