"""Versioned JSON store for the payload-only literal fallback snapshot."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION = 1


class LiteralFallbackSnapshotDataError(ValueError):
    """The published fallback snapshot is invalid and must fail closed."""


class LiteralFallbackSnapshotData(NamedTuple):
    entries: list[dict[str, Any]]
    index_version: int
    saved_at_unix: float


class LiteralFallbackSnapshotFileStore:
    """Publish a complete snapshot by atomic replace; never modify it in place.

    A snapshot contains only durable point IDs, profile keys and payloads. It
    never contains embedding vectors and never invents a candidate.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not str(self._path):
            raise ValueError("snapshot path is required")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> LiteralFallbackSnapshotData:
        if not self._path.is_file():
            raise FileNotFoundError(self._path)

        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise LiteralFallbackSnapshotDataError(
                "literal fallback snapshot cannot be parsed"
            ) from error

        if not isinstance(document, dict):
            raise LiteralFallbackSnapshotDataError("snapshot must be an object")
        if document.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise LiteralFallbackSnapshotDataError("snapshot schema version is invalid")

        index_version = _nonnegative_int(document.get("index_version"))
        saved_at = _float(document.get("saved_at_unix"), default=0.0)
        points = document.get("points")
        if not isinstance(points, list):
            raise LiteralFallbackSnapshotDataError("snapshot points must be an array")
        if document.get("point_count") != len(points):
            raise LiteralFallbackSnapshotDataError("snapshot point count is invalid")

        entries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for point in points:
            if not isinstance(point, dict):
                raise LiteralFallbackSnapshotDataError("snapshot point must be an object")
            if set(point) != {"id", "profile_key", "payload"}:
                raise LiteralFallbackSnapshotDataError("snapshot point fields are invalid")
            point_id = _nonempty_str(point.get("id"))
            profile_key = _nonempty_str(point.get("profile_key"))
            payload = point.get("payload")
            if not isinstance(payload, dict):
                raise LiteralFallbackSnapshotDataError("snapshot payload must be an object")
            if point_id in seen_ids:
                raise LiteralFallbackSnapshotDataError("snapshot point ID is duplicated")
            seen_ids.add(point_id)
            entries.append(
                {
                    "id": point_id,
                    "profile_key": profile_key,
                    "payload": dict(payload),
                }
            )
        return LiteralFallbackSnapshotData(
            entries=entries,
            index_version=index_version,
            saved_at_unix=saved_at,
        )

    def save(self, *, entries: list[dict[str, Any]], index_version: int) -> None:
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise LiteralFallbackSnapshotDataError("entry must be an object")
            if set(entry) != {"id", "profile_key", "payload"}:
                raise LiteralFallbackSnapshotDataError("entry fields are invalid")
            point_id = _nonempty_str(entry.get("id"))
            profile_key = _nonempty_str(entry.get("profile_key"))
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                raise LiteralFallbackSnapshotDataError("entry payload must be an object")
            if point_id in seen_ids:
                raise LiteralFallbackSnapshotDataError("entry point ID is duplicated")
            seen_ids.add(point_id)
            normalized.append(
                {
                    "id": point_id,
                    "profile_key": profile_key,
                    "payload": dict(payload),
                }
            )

        document = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "index_version": _nonnegative_int(index_version),
            "saved_at_unix": time.time(),
            "point_count": len(normalized),
            "points": normalized,
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        logger.info(
            "[LITERAL-SNAPSHOT] published points=%d index_version=%d",
            len(normalized),
            int(index_version),
        )


def _nonempty_str(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise LiteralFallbackSnapshotDataError("snapshot string field is invalid")
    return value


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LiteralFallbackSnapshotDataError("snapshot integer field is invalid")
    return value


def _float(value: Any, *, default: float) -> float:
    if isinstance(value, bool):
        raise LiteralFallbackSnapshotDataError("snapshot numeric field is invalid")
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        if value is None:
            return default
        raise LiteralFallbackSnapshotDataError(
            "snapshot numeric field is invalid"
        ) from error


__all__ = [
    "LiteralFallbackSnapshotData",
    "LiteralFallbackSnapshotDataError",
    "LiteralFallbackSnapshotFileStore",
]
