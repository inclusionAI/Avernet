"""Offload an oversized ``config_artifact`` out of the ``ac_bot_publish.ext``
column and back.

The published ``config_artifact`` (a serialized ``BotConfigArtifact``) rides
inside the ``ac_bot_publish.ext`` JSON, which is stored in a ``TEXT`` column
capped at ~64 KB. A richly-configured teclaw bot can serialize past that. When
it does, :class:`ConfigArtifactOffloader` writes the artifact's JSON to object
storage and replaces it inline with a small self-describing marker, then
re-inlines it transparently on read — so the repository's callers always see a
plain ``ext['config_artifact']`` regardless of where the bytes actually live.

This is the whole ext ⇄ object-storage transform, split out of the repository so
the repository owns only persistence. It is a DI component holding the injected
:class:`ObjectStoragePlugin`; every record-specific input (``publish_id``,
``env``) is passed per call.

Design invariants:

* **Mutual exclusion by construction.** :meth:`prepare` drops BOTH the inline
  ``config_artifact`` and its marker up front, then writes back exactly one. A
  stored ext therefore never carries both, so a stale marker can never shadow a
  freshly written inline artifact and the read path needs no both-present case.
* **Content-addressed, immutable objects.** Each offload writes a NEW object
  keyed by a content digest, never an in-place overwrite. A rejected
  optimistic-lock write or a concurrent writer can never clobber the bytes a
  still-valid record points at. Superseded versions are reaped together by
  :meth:`cleanup` when the record is deleted.
* **No I/O in :meth:`prepare`.** It returns a pending upload the caller performs
  only AFTER the DB write is confirmed to persist, so a rejected write leaves no
  orphan object.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from injector import inject

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin

logger = get_logger()

# 60 KB leaves ~4 KB of headroom under the 65535-byte TEXT cap for the ext's
# sibling fields (binding, migration_path, …).
ARTIFACT_OSS_THRESHOLD_BYTES = 60 * 1024
# The ext key holding the inline artifact, and — when offloaded — its marker.
ARTIFACT_KEY = "config_artifact"
ARTIFACT_OSS_MARKER = "config_artifact_oss"

# (oss_key, artifact_json) — an artifact object waiting to be written.
PendingUpload = Tuple[str, str]


class ConfigArtifactOffloader:
    """Move an oversized ``ext['config_artifact']`` to object storage and back."""

    @inject
    def __init__(self, oss: ObjectStoragePlugin) -> None:
        self._oss = oss

    # ── keys ────────────────────────────────────────────────────

    def prefix(self, env: str, publish_id: int) -> str:
        """Per-record object-storage prefix. Every offloaded version of one
        publish record lives under here; :meth:`cleanup` sweeps the whole
        subtree so superseded versions never accumulate."""
        return f"teclaw/{env}/bot_publish/{publish_id}/"

    def _key(self, env: str, publish_id: int, digest: str) -> str:
        """Content-addressed key for one artifact version (see module invariants)."""
        return f"{self.prefix(env, publish_id)}config_artifact-{digest}.json"

    # ── write ───────────────────────────────────────────────────

    def prepare(
        self, ext: Optional[Dict[str, Any]], publish_id: int, env: str
    ) -> Tuple[Optional[str], Optional[PendingUpload]]:
        """Produce the JSON string to store in the ext column, plus a pending
        object-storage upload.

        Returns ``(ext_json, pending)``; ``pending`` is a :data:`PendingUpload`
        to write via :meth:`upload`, or ``None``. Performs NO I/O — the caller
        uploads ``pending`` only after confirming the DB write will persist.
        """
        if ext is None:
            return None, None
        # Erase both artifact keys, then fill in the single one that applies.
        base = {
            k: v
            for k, v in ext.items()
            if k not in (ARTIFACT_KEY, ARTIFACT_OSS_MARKER)
        }
        artifact = ext.get(ARTIFACT_KEY)
        if artifact is None:
            return json.dumps(base, ensure_ascii=False), None
        artifact_json = json.dumps(artifact, ensure_ascii=False)
        size = len(artifact_json.encode("utf-8"))
        if size <= ARTIFACT_OSS_THRESHOLD_BYTES:
            base[ARTIFACT_KEY] = artifact
            return json.dumps(base, ensure_ascii=False), None
        digest = hashlib.sha1(artifact_json.encode("utf-8")).hexdigest()[:12]
        key = self._key(env, publish_id, digest)
        base[ARTIFACT_OSS_MARKER] = {
            "offloaded": True,
            "oss_key": key,
            "size_bytes": size,
            "threshold_bytes": ARTIFACT_OSS_THRESHOLD_BYTES,
            "note": (
                f"config_artifact ({size} bytes) exceeded the "
                f"{ARTIFACT_OSS_THRESHOLD_BYTES}-byte inline limit for the "
                "ac_bot_publish.ext TEXT column and was stored in object storage "
                f"at oss_key; the repository re-inlines it as ext['{ARTIFACT_KEY}'] "
                "on read."
            ),
        }
        return json.dumps(base, ensure_ascii=False), (key, artifact_json)

    def upload(self, pending: Optional[PendingUpload]) -> None:
        """Write a prepared artifact object. Fail loud on error — better than
        silently truncating the ext column or shipping a dangling marker."""
        if pending is None:
            return
        key, body = pending
        if not self._oss.put_object(key, body):
            raise RuntimeError(
                f"config_artifact offload failed: put_object({key!r}) "
                "returned False"
            )

    # ── read ────────────────────────────────────────────────────

    def resolve(
        self, ext: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Inverse of :meth:`prepare`.

        If ``ext`` carries the offload marker, fetch the artifact JSON back and
        re-inline it as ``ext['config_artifact']``, dropping the marker so
        callers see the same shape as an inline artifact.

        Fail loud: a marker whose object cannot be fetched (missing ``oss_key``
        or ``get_object`` returning ``None``) raises. Returning a record silently
        missing its ``config_artifact`` would let a boot proceed on a corrupt
        config — a hard error the caller must see.
        """
        if not ext or ARTIFACT_OSS_MARKER not in ext:
            return ext
        marker = ext[ARTIFACT_OSS_MARKER]
        key = marker.get("oss_key") if isinstance(marker, dict) else None
        raw = self._oss.get_object(key) if key else None
        if raw is None:
            raise RuntimeError(
                "config_artifact fetch failed: offloaded artifact unreadable "
                f"at oss_key={key!r}"
            )
        resolved = {k: v for k, v in ext.items() if k != ARTIFACT_OSS_MARKER}
        resolved[ARTIFACT_KEY] = json.loads(
            raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        )
        return resolved

    # ── delete cleanup ──────────────────────────────────────────

    def cleanup(self, prefix: str) -> None:
        """Best-effort sweep of every object under ``prefix`` (current +
        superseded versions). Must never raise — cleanup failing is not allowed
        to fail the caller's DB delete; a transport error is logged and dropped.
        """
        try:
            for key in self._oss.list_objects(prefix):
                self._oss.delete_object(key)
        except Exception:  # noqa: BLE001 - best-effort artifact cleanup
            logger.exception(
                "[ConfigArtifactOffloader] artifact cleanup failed for "
                "prefix=%s", prefix,
            )


__all__ = [
    "ConfigArtifactOffloader",
    "ARTIFACT_KEY",
    "ARTIFACT_OSS_MARKER",
    "ARTIFACT_OSS_THRESHOLD_BYTES",
    "PendingUpload",
]
