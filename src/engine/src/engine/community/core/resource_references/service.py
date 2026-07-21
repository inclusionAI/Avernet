"""Validate structured references and rewrite Chat placeholders."""
from __future__ import annotations

import hashlib
import html
import logging
import re
from pathlib import Path
from typing import Callable

from engine.community.core.resource_materialization.models import hash_identifier
from engine.community.core.resource_materialization.service import ManifestStore
from engine.community.core.resource_references.models import ResolvedResourceContext
from engine.community.plugin_api.workspace_root import workspace_root_strict

log = logging.getLogger("engine.resource_reference")
_PLACEHOLDER = re.compile(
    r'<file-ref\s+insert_id="(?P<insert_id>[A-Za-z0-9._-]+)"\s*></file-ref>'
)
_FORBIDDEN_PATH_KEYS = {
    "path",
    "workspace_path",
    "workspacePath",
    "device_path",
    "devicePath",
    "canonical_bot_absolute_path",
}


class ResourceReferenceError(ValueError):
    """A Chat reference failed authorization or workspace validation."""


class ResourceReferenceService:
    def __init__(
        self,
        *,
        workspace_root_provider: Callable[[], Path | None] = workspace_root_strict,
    ) -> None:
        self._workspace_root_provider = workspace_root_provider

    def rewrite(
        self,
        prompt: str,
        session_key: str,
        resource_references: list[dict] | None,
        prompt_file_refs: list[dict] | None,
    ) -> ResolvedResourceContext:
        references = resource_references or []
        placeholders = list(_PLACEHOLDER.finditer(prompt))
        if not references and not placeholders:
            return ResolvedResourceContext(prompt=prompt)
        if prompt.count("<file-ref") != len(placeholders):
            raise ResourceReferenceError("invalid_file_ref_placeholder")

        mapping = self._reference_mapping(references)
        placeholder_ids = [match.group("insert_id") for match in placeholders]
        if len(placeholder_ids) != len(set(placeholder_ids)):
            raise ResourceReferenceError("duplicate_insert_id")
        if set(placeholder_ids) != set(mapping):
            raise ResourceReferenceError("insert_id_mismatch")
        if prompt_file_refs is not None:
            prompt_mapping = self._reference_mapping(prompt_file_refs)
            if prompt_mapping != mapping:
                raise ResourceReferenceError("prompt_file_refs_mismatch")

        root = self._workspace_root()
        store = ManifestStore(root)
        expected_session_hash = hash_identifier(session_key)
        replacements: dict[str, str] = {}
        materialized: list[dict] = []
        for insert_id, resource_id in mapping.items():
            entry = store.get(resource_id)
            if entry is None or entry.status != "ready":
                raise ResourceReferenceError("resource_not_ready")
            if entry.session_key_hash != expected_session_hash:
                raise ResourceReferenceError("cross_session_resource")
            relative = Path(entry.relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ResourceReferenceError("path_mismatch")
            target = root / relative
            # COSEC: resolve symlinks and compare Path parents before exposing
            # the Bot absolute path to a model adapter.
            canonical = target.resolve(strict=False)
            if canonical != root and root not in canonical.parents:
                raise ResourceReferenceError("path_mismatch")
            if not canonical.is_file():
                raise ResourceReferenceError("workspace_file_missing")
            if canonical.stat().st_size != entry.size_bytes:
                raise ResourceReferenceError("size_mismatch")
            if self._sha256(canonical) != entry.content_hash:
                raise ResourceReferenceError("content_hash_mismatch")
            replacement = (
                '<file-ref name="{}" path="{}"></file-ref>'.format(
                    html.escape(entry.filename, quote=True),
                    html.escape(str(canonical), quote=True),
                )
            )
            replacements[insert_id] = replacement
            materialized.append(
                {
                    "resource_id": resource_id,
                    "insert_id": insert_id,
                    "filename": entry.filename,
                    "canonical_bot_absolute_path": str(canonical),
                    "content_hash": entry.content_hash,
                    "size_bytes": entry.size_bytes,
                }
            )

        rewritten = _PLACEHOLDER.sub(
            lambda match: replacements[match.group("insert_id")],
            prompt,
        )
        log.info(
            "engine.prompt_ref.rewrite session_key_hash=%s reference_count=%s",
            expected_session_hash[:16],
            len(materialized),
        )
        return ResolvedResourceContext(
            prompt=rewritten,
            resource_references=references,
            materialized_files=materialized,
        )

    @staticmethod
    def _reference_mapping(references: list[dict]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for value in references:
            if not isinstance(value, dict):
                raise ResourceReferenceError("invalid_resource_reference")
            if _FORBIDDEN_PATH_KEYS.intersection(value):
                raise ResourceReferenceError("caller_path_forbidden")
            insert_id = value.get("insert_id") or value.get("insertId")
            resource_id = value.get("resource_id") or value.get("resourceId")
            if not isinstance(insert_id, str) or not isinstance(resource_id, str):
                raise ResourceReferenceError("invalid_resource_reference")
            if not re.fullmatch(r"[A-Za-z0-9._-]+", insert_id):
                raise ResourceReferenceError("invalid_insert_id")
            if not re.fullmatch(r"[A-Za-z0-9._-]+", resource_id):
                raise ResourceReferenceError("invalid_resource_id")
            if insert_id in mapping:
                raise ResourceReferenceError("duplicate_insert_id")
            mapping[insert_id] = resource_id
        return mapping

    def _workspace_root(self) -> Path:
        value = self._workspace_root_provider()
        if value is None:
            raise ResourceReferenceError("workspace_root_not_configured")
        root = Path(value)
        if not root.is_absolute():
            raise ResourceReferenceError("workspace_root_not_absolute")
        try:
            return root.resolve(strict=True)
        except OSError as exc:
            raise ResourceReferenceError("workspace_root_missing") from exc

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
