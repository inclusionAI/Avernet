"""Transport-independent search index readiness metadata."""

from __future__ import annotations

from typing import Any, Mapping

INDEX_STATE_FIELDS = (
    "index_size",
    "index_pending_count",
    "index_complete",
    "index_backfill_running",
    "index_backfill_failed",
    "index_backfill_error_type",
    "index_state_unavailable",
    "index_version",
    "index_lag_seconds",
    "index_stale",
)


def _provider_failure() -> dict[str, Any]:
    """Return the stable unavailable state without provider details."""
    return {
        "index_complete": False,
        "index_pending_count": None,
        "index_state_unavailable": True,
    }


def search_index_state_metadata(registry: Any) -> dict[str, Any]:
    """Build bounded, non-probing index state for search and recommendation."""

    try:
        vector_store = registry.get("vector_store")
        backfill_status = registry.get("index_backfill_status")
    except Exception:
        return _provider_failure()

    if vector_store is None:
        return {
            "index_complete": False,
            "index_pending_count": None,
            "index_state_unavailable": True,
        }

    try:
        backfill = (
            backfill_status.snapshot()
            if backfill_status is not None
            else {"running": False, "failed": False, "error_type": None}
        )
    except Exception:
        return _provider_failure()

    index_stale = bool(getattr(vector_store, "index_stale", False))
    backfill_running = bool(backfill["running"])
    backfill_failed = bool(backfill["failed"])
    state_unavailable = bool(
        getattr(vector_store, "index_state_unavailable", False)
    )
    startup_rebuild_failed = bool(
        getattr(vector_store, "startup_rebuild_failed", False)
    )

    metadata: dict[str, Any] = {
        "index_size": getattr(vector_store, "index_size", None),
        "index_pending_count": getattr(
            vector_store, "index_pending_count", None
        ),
        "index_backfill_running": backfill_running,
        "index_backfill_failed": backfill_failed,
        "index_backfill_error_type": (
            backfill["error_type"] if backfill_failed else None
        ),
        "index_state_unavailable": state_unavailable,
        "index_version": getattr(vector_store, "index_version", None),
        "index_lag_seconds": getattr(
            vector_store, "index_lag_seconds", None
        ),
        "index_stale": index_stale,
    }
    metadata["index_complete"] = not any(
        (
            backfill_running,
            backfill_failed,
            state_unavailable,
            startup_rebuild_failed,
            index_stale,
        )
    )
    return metadata


def index_state_from_provider(provider: Any) -> dict[str, Any] | None:
    """Read the provider's bounded state and convert probe failure explicitly."""

    if provider is None:
        return None
    try:
        return dict(provider())
    except Exception:
        return {
            "index_complete": False,
            "index_state_unavailable": True,
        }


def project_index_state(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable index-state subset without provider errors."""
    return {
        key: metadata.get(key)
        for key in INDEX_STATE_FIELDS
        if key in metadata
    }


__all__ = [
    "INDEX_STATE_FIELDS",
    "index_state_from_provider",
    "project_index_state",
    "search_index_state_metadata",
]
