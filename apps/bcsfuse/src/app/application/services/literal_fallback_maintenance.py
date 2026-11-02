"""Maintain the final literal fallback while vector writes succeed."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from search_core.plugin_api.vector_point import VectorPoint as CoreVectorPoint
from search_core.plugin_api.vector_search_hit import VectorSearchHit
from search_core.providers.fallback import RegexFallbackIndex

from app.domain.services.vector_store_adapter import VectorStoreAdapter

logger = logging.getLogger(__name__)


def _profile_key(point: Any) -> str | None:
    payload = point.payload or {}
    key = payload.get("profile_key")
    if key:
        return str(key)
    worker_id = payload.get("worker_id") or payload.get("staff_id")
    profile_id = payload.get("profile_id")
    return f"{worker_id}:{profile_id}" if worker_id and profile_id else None


def _text_length(payload: dict) -> int:
    return len(str(
        payload.get("content")
        or payload.get("searchable_text")
        or payload.get("content_preview")
        or ""
    ))


class LiteralFallbackSnapshot:
    """In-process point view, deduplicated to the richest payload per profile."""

    def __init__(self) -> None:
        # Physical point IDs retain fragment identity so updates and deletes are exact.
        self._physical: dict[str, dict[str, Any]] = {}
        self._index = RegexFallbackIndex([])
        self._selected_view: dict[str, dict[str, Any]] = {}
        self._direct_aliases: dict[str, set[str]] = {}
        self.version = 0
        self.snapshot_index_version = 0
        self.snapshot_saved_at_unix = 0.0
        self.source = "unloaded"
        self.created_at = time.monotonic()
        self.updated_at = time.monotonic()

    def clear(self) -> None:
        self._physical.clear()
        self._reindex()

    def upsert(self, points: list[Any]) -> None:
        changed = False
        for point in points:
            profile_key = _profile_key(point)
            if profile_key is None:
                continue
            payload = dict(point.payload or {})
            self._physical[str(point.id)] = {"profile_key": profile_key, "payload": payload}
            changed = True
        if changed:
            self._reindex()

    def upsert_raw(self, point_id: str, payload: dict) -> None:
        """Update legacy id/vector/metadata stores without copying vectors."""
        profile_key = payload.get("profile_key")
        if not profile_key:
            worker_id = payload.get("worker_id") or payload.get("staff_id")
            profile_id = payload.get("profile_id")
            profile_key = f"{worker_id}:{profile_id}" if worker_id and profile_id else None
        if not profile_key:
            return
        self._physical[str(point_id)] = {
            "profile_key": str(profile_key),
            "payload": dict(payload),
        }
        self._reindex()

    def remove_ids(self, point_ids: list[str]) -> None:
        removed = [str(point_id) for point_id in point_ids if str(point_id) in self._physical]
        for point_id in removed:
            self._physical.pop(point_id)
        if removed:
            self._reindex()

    def update_worker_payload(self, worker_id: str, payload_updates: dict) -> None:
        changed = False
        for entry in self._physical.values():
            if entry["payload"].get("worker_id") == worker_id:
                entry["payload"] = {**entry["payload"], **payload_updates}
                changed = True
        if changed:
            self._reindex()

    def remove_worker(self, worker_id: str) -> None:
        removed = [
            key for key, entry in self._physical.items()
            if entry["payload"].get("worker_id") == worker_id
        ]
        self.remove_ids(removed)

    def remove_profile(self, worker_id: str, profile_id: str) -> None:
        removed = [
            key for key, entry in self._physical.items()
            if entry["payload"].get("worker_id") == worker_id
            and entry["payload"].get("profile_id") == profile_id
        ]
        self.remove_ids(removed)

    def reload_from_store(self, store: VectorStoreAdapter) -> None:
        load_fallback_points = getattr(store, "load_fallback_points", None)
        if callable(load_fallback_points):
            points = load_fallback_points()
            physical: dict[str, dict[str, Any]] = {}
            for point in points:
                profile_key = _profile_key(point)
                if profile_key is None:
                    continue
                physical[str(point.id)] = {
                    "profile_key": profile_key,
                    "payload": dict(point.payload or {}),
                }
            self._physical = physical
            self._reindex()
            observed_version = int(getattr(store, "index_version", 0) or 0)
            if observed_version >= self.snapshot_index_version:
                self.snapshot_index_version = observed_version
            self.source = "durable"
            return

        point_ids = store.get_vector_ids()
        physical = {}
        for point_id in point_ids:
            point = store.get(point_id)
            profile_key = _profile_key(point) if point is not None else None
            if profile_key is None:
                continue
            physical[str(point_id)] = {
                "profile_key": profile_key,
                "payload": dict(point.payload or {}),
            }
        self._physical = physical
        self._reindex()
        self.source = "durable"

    def export_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "id": point_id,
                "profile_key": entry["profile_key"],
                "payload": dict(entry["payload"]),
            }
            for point_id, entry in self._physical.items()
        ]

    def restore_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        index_version: int,
        saved_at_unix: float = 0.0,
    ) -> None:
        physical: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("snapshot entry must be an object")
            point_id = str(entry.get("id") or "")
            profile_key = str(entry.get("profile_key") or "")
            payload = entry.get("payload")
            if not point_id or not profile_key or not isinstance(payload, dict):
                raise ValueError("snapshot entry is invalid")
            physical[point_id] = {
                "profile_key": profile_key,
                "payload": dict(payload),
            }
        self._physical = physical
        self._reindex()
        self.version = max(0, int(index_version))
        self.snapshot_index_version = self.version
        self.snapshot_saved_at_unix = max(0.0, float(saved_at_unix))
        self.source = "file"

    def _reindex(self) -> None:
        selected = self._selected_profiles()
        self._selected_view = selected
        self._direct_aliases = self._direct_alias_index(selected)
        self._index = RegexFallbackIndex([
            CoreVectorPoint(
                id=profile_key,
                vector=[],
                payload=dict(payload),
            )
            for profile_key, payload in selected.items()
        ])
        self.version += 1
        self.updated_at = time.monotonic()

    @staticmethod
    def _direct_alias_index(
        selected: dict[str, dict[str, Any]],
    ) -> dict[str, set[str]]:
        aliases: dict[str, set[str]] = {}
        for profile_key, payload in selected.items():
            values = {
                profile_key,
                payload.get("profile_key"),
                payload.get("worker_id"),
                payload.get("staff_id"),
                payload.get("id"),
            }
            for value in values:
                normalized = str(value or "").strip().casefold()
                if normalized:
                    aliases.setdefault(normalized, set()).add(profile_key)
        return aliases


    def _selected_profiles(self) -> dict[str, dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for entry in self._physical.values():
            profile_key = entry["profile_key"]
            payload = entry["payload"]
            previous = selected.get(profile_key)
            if previous is None or _text_length(payload) > _text_length(previous):
                selected[profile_key] = payload
        return selected

    @property
    def size(self) -> int:
        return len(self._physical)

    @property
    def profile_count(self) -> int:
        return len(self._selected_view)

    def keyword_search(self, query, limit, filters, allowed_ids):
        return self._index.keyword_search(query, limit, filters, allowed_ids)

    def exact_search(self, query, limit, filters, allowed_ids=None):
        """Return only exact identifier matches from the in-process view."""

        target = str(query or "").strip().casefold()
        if not target or limit <= 0 or allowed_ids == frozenset():
            return []
        profile_keys = self._direct_aliases.get(target)
        if not profile_keys:
            return []
        matches: list[tuple[str, dict[str, Any]]] = []
        for profile_key in profile_keys:
            if allowed_ids is not None and profile_key not in allowed_ids:
                continue
            payload = self._selected_view.get(profile_key)
            if payload is None:
                continue
            if self._payload_matches_filters(payload, filters):
                matches.append((profile_key, dict(payload)))
        matches.sort(key=lambda item: item[0])
        return [
            VectorSearchHit(
                id=profile_key,
                score=1.0,
                payload=payload,
            )
            for profile_key, payload in matches[:limit]
        ]

    @staticmethod
    def _payload_matches_filters(payload: dict[str, Any], filters: Any) -> bool:
        for key, expected in (filters or {}).items():
            if key.startswith("_"):
                return False
            actual = payload.get(key)
            actual_values = actual if isinstance(actual, list) else [actual]
            expected_values = (
                expected
                if isinstance(expected, (list, tuple, set, frozenset))
                else [expected]
            )
            if not any(str(value) in {str(item) for item in expected_values} for value in actual_values):
                return False
        return True

    def default_search(self, limit, filters, allowed_ids=None):
        return self._index.default_search(limit, filters, allowed_ids)


class LiteralFallbackMaintainingVectorStore:
    """Proxy that maintains a local fallback only after durable writes succeed."""

    def __init__(
        self,
        backend: VectorStoreAdapter,
        fallback: LiteralFallbackSnapshot,
        snapshot_store: Any | None = None,
    ):
        self._backend = backend
        self.literal_fallback_index = fallback
        self._snapshot_store = snapshot_store
        self._snapshot_publish_lock = threading.Lock()
        self.snapshot_publish_error_type: str | None = None

    @property
    def backend(self) -> VectorStoreAdapter:
        return self._backend

    def _publish_snapshot_once_owned(self) -> None:
        """Publish only from the lease owner so shared snapshot writes do not race."""

        if self._snapshot_store is None or getattr(self._backend, "read_only", False):
            return
        with self._snapshot_publish_lock:
            try:
                self._snapshot_store.save(
                    entries=self.literal_fallback_index.export_entries(),
                    index_version=int(
                        getattr(self._backend, "index_version", 0)
                        or self.literal_fallback_index.snapshot_index_version
                    ),
                )
                self.snapshot_publish_error_type = None
            except Exception as error:
                self.snapshot_publish_error_type = type(error).__name__
                logger.warning(
                    "[LITERAL-FALLBACK] snapshot publication failed: %s",
                    type(error).__name__,
                )

    def index_dependency_readiness(self) -> dict[str, bool]:
        """Forward partial readiness while maintaining the local literal view."""
        result = dict(self._backend.index_dependency_readiness())
        # IndexState may acknowledge or rebuild a newly observed revision during
        # the same probe. Reload the process-local literal view after that
        # recovery without probing Qdrant twice.
        if result.pop("index_revision_recovered", False):
            self.literal_fallback_index.reload_from_store(self._backend)
            self._publish_snapshot_once_owned()
        return result

    def check_ready(self) -> bool:
        # Some adapters use ``None`` for a successful probe; only an explicit
        # false value means unavailable.
        if self._backend.check_ready() is False:
            return False
        if getattr(self._backend, "index_revision_stale", False):
            if getattr(self._backend, "read_only", False):
                self.synchronize_read_view()
            else:
                self.rebuild_from_backend()
        return not bool(getattr(self._backend, "index_stale", False))

    def upsert(self, *args: Any, **kwargs: Any):
        result = self._backend.upsert(*args, **kwargs)
        supplied = args[0] if args else kwargs.get("points")
        if isinstance(supplied, str):
            metadata = args[2] if len(args) > 2 else kwargs.get("metadata", {})
            self.literal_fallback_index.upsert_raw(supplied, dict(metadata or {}))
            self._publish_snapshot_once_owned()
        elif supplied:
            self.literal_fallback_index.upsert(supplied)
            self._publish_snapshot_once_owned()
        return result

    def delete(self, ids):
        result = self._backend.delete(ids)
        normalized = [ids] if isinstance(ids, str) else list(ids)
        self.literal_fallback_index.remove_ids(normalized)
        self._publish_snapshot_once_owned()
        return result

    def update_payload_by_worker(self, worker_id, payload_updates):
        result = self._backend.update_payload_by_worker(worker_id, payload_updates)
        self.literal_fallback_index.update_worker_payload(worker_id, payload_updates)
        self._publish_snapshot_once_owned()
        return result

    def delete_by_worker(self, worker_id):
        result = self._backend.delete_by_worker(worker_id)
        self.literal_fallback_index.remove_worker(worker_id)
        self._publish_snapshot_once_owned()
        return result

    def delete_by_profile(self, worker_id, profile_id):
        result = self._backend.delete_by_profile(worker_id, profile_id)
        self.literal_fallback_index.remove_profile(worker_id, profile_id)
        self._publish_snapshot_once_owned()
        return result

    def clear(self):
        result = self._backend.clear()
        self.literal_fallback_index.clear()
        self._publish_snapshot_once_owned()
        return result

    def synchronize_read_view(self, *args: Any, **kwargs: Any):
        """Refresh read-local projections without mutating another owner's index."""
        result = self._backend.synchronize_read_view(*args, **kwargs)
        if getattr(self._backend, "read_only", False):
            self.literal_fallback_index.reload_from_store(self._backend)
        return result

    def rebuild_from_backend(self, *args: Any, **kwargs: Any):
        if getattr(self._backend, "read_only", False):
            return self.synchronize_read_view(*args, **kwargs)
        result = self._backend.rebuild_from_backend(*args, **kwargs)
        self.literal_fallback_index.reload_from_store(self._backend)
        self._publish_snapshot_once_owned()
        return result

    def sync_incremental(self, *args: Any, **kwargs: Any):
        if getattr(self._backend, "read_only", False):
            return self.synchronize_read_view(*args, **kwargs)
        result = self._backend.sync_incremental(*args, **kwargs)
        self.literal_fallback_index.reload_from_store(self._backend)
        self._publish_snapshot_once_owned()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)


def wrap_vector_store_with_literal_fallback(
    store: VectorStoreAdapter,
    *,
    snapshot_store: Any | None = None,
) -> LiteralFallbackMaintainingVectorStore:
    """Wrap a store with an incrementally maintained, publishable fallback."""
    fallback = LiteralFallbackSnapshot()
    try:
        fallback.reload_from_store(store)
        wrapper = LiteralFallbackMaintainingVectorStore(
            store,
            fallback,
            snapshot_store=snapshot_store,
        )
        wrapper._publish_snapshot_once_owned()
        return wrapper
    except Exception as durable_error:
        logger.warning(
            "[LITERAL-FALLBACK] durable snapshot load failed; "
            "trying a published file: %s",
            type(durable_error).__name__,
        )

    if snapshot_store is None:
        return LiteralFallbackMaintainingVectorStore(store, fallback)

    try:
        published = snapshot_store.load()
        fallback.restore_entries(
            published.entries,
            index_version=published.index_version,
            saved_at_unix=published.saved_at_unix,
        )
    except Exception as file_error:
        logger.warning(
            "[LITERAL-FALLBACK] published snapshot unavailable: %s",
            type(file_error).__name__,
        )
    return LiteralFallbackMaintainingVectorStore(
        store,
        fallback,
        snapshot_store=snapshot_store,
    )


__all__ = [
    "LiteralFallbackMaintainingVectorStore",
    "LiteralFallbackSnapshot",
    "wrap_vector_store_with_literal_fallback",
]
