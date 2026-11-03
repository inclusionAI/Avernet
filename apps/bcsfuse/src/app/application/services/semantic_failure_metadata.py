"""Shared semantic-provider failure diagnostics for application services."""

from __future__ import annotations

from typing import Any


def _provider_status_code(error: BaseException) -> int | None:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and status_code > 0:
        return status_code
    return None


def _retry_after_seconds(error: BaseException) -> float | None:
    retry_after = getattr(error, "retry_after_seconds", None)
    if isinstance(retry_after, (int, float)):
        return max(0.0, float(retry_after))
    response = getattr(error, "response", None)
    header = None
    if response is not None:
        try:
            header = response.headers.get("Retry-After")
        except Exception:
            header = None
    if header is None:
        return None
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        return None


def annotate_semantic_failure(metadata: dict[str, Any]) -> None:
    """Publish the stable semantic reason on the fallback envelope."""

    if (
        metadata.get("semantic_unavailable") is True
        and metadata.get("semantic_status_code") == 429
        and not metadata.get("semantic_reason_code")
    ):
        metadata["semantic_reason_code"] = "embedding_quota_exhausted"


def semantic_failure_metadata(error: BaseException) -> dict[str, Any]:
    status_code = _provider_status_code(error)
    reason_code = getattr(error, "reason_code", None)
    if status_code == 429 and not reason_code:
        reason_code = "embedding_quota_exhausted"
    provider_reason_code = getattr(error, "provider_reason_code", None)
    return {
        "semantic_error": type(error).__name__,
        "semantic_status_code": status_code,
        "semantic_retry_after_seconds": _retry_after_seconds(error),
        "semantic_reason_code": reason_code,
        "semantic_provider_reason_code": provider_reason_code,
    }


__all__ = [
    "annotate_semantic_failure",
    "semantic_failure_metadata",
]
