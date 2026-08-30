"""OSS adapter for the immutable exact-version Canonical Center Store."""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreConfig,
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
)
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectCreateResult,
    ObjectReadStatus,
    ObjectStoragePlugin,
)


class OssCanonicalCenterVersionStore:
    """Publish exact files with integrity metadata outside the Runtime tree."""

    def __init__(
        self,
        *,
        object_storage: ObjectStoragePlugin,
        config: CanonicalCenterStoreConfig,
    ) -> None:
        if not isinstance(object_storage, ImmutableObjectStorageCapability):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION,
                "Canonical Center Store requires atomic create-if-absent and "
                "three-state reads",
            )
        self._immutable_objects = object_storage
        self._config = config

    def write_version(
        self, version: CanonicalCenterVersion
    ) -> CanonicalCenterVersionRef:
        # Rebuild at the trust boundary even though the public value objects
        # validate themselves. This keeps a forged/subclassed value from ever
        # shaping an object key.
        version = CanonicalCenterVersion.from_files(
            version.identity,
            version.file_map,
        )
        ref = CanonicalCenterVersionRef(version.identity)
        root = self._root(version.identity)
        control_root = self._control_root(version.identity)
        intent_key = f"{control_root}/write-intent.json"
        manifest_key = f"{control_root}/content-manifest.json"
        intent_body = self._intent_body(version)
        intent_result = self._immutable_objects.create_object_if_absent(
            intent_key, intent_body
        )
        if intent_result is ObjectCreateResult.ALREADY_EXISTS:
            existing = self._read_required(intent_key)
            if existing != intent_body:
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.CONTENT_CONFLICT,
                    f"exact version identity already belongs to other content: {ref.locator}",
                )
        elif intent_result is ObjectCreateResult.FAILED:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.WRITE_FAILED,
                f"failed to reserve exact version identity: {ref.locator}",
            )

        existing_manifest = self._read_manifest(ref, missing_is_error=False)
        if existing_manifest is not None:
            if existing_manifest != version.manifest:
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.CONTENT_CONFLICT,
                    f"integrity manifest conflicts at exact identity: {ref.locator}",
                )
            self.read_version(ref)
            return ref

        for item in version.files:
            key = f"{root}/{item.path}"
            result = self._immutable_objects.create_object_if_absent(
                key, item.content
            )
            if result is ObjectCreateResult.FAILED:
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.WRITE_FAILED,
                    f"failed to write canonical file: {item.path}",
                )
            if (
                result is ObjectCreateResult.ALREADY_EXISTS
                and self._read_required(key) != item.content
            ):
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.CONTENT_CONFLICT,
                    f"canonical file conflicts at exact identity: {item.path}",
                )

        # Verify every immutable Runtime file before publishing the out-of-band
        # integrity manifest. Domain publication remains owned by PUBLISHED.
        for item in version.files:
            if self._read_required(f"{root}/{item.path}") != item.content:
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.CORRUPT_CONTENT,
                    f"canonical file failed prepublish verification: {item.path}",
                )

        manifest_result = self._immutable_objects.create_object_if_absent(
            manifest_key, version.manifest
        )
        if manifest_result is ObjectCreateResult.FAILED:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.WRITE_FAILED,
                f"failed to publish integrity manifest: {ref.locator}",
            )
        if (
            manifest_result is ObjectCreateResult.ALREADY_EXISTS
            and self._read_required(manifest_key) != version.manifest
        ):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.CONTENT_CONFLICT,
                f"integrity manifest conflicts at exact identity: {ref.locator}",
            )
        return ref

    def read_version(
        self, ref: CanonicalCenterVersionRef
    ) -> CanonicalCenterVersion:
        manifest = self._read_manifest(ref, missing_is_error=True)
        if manifest is None:
            raise AssertionError("missing manifest must raise")
        entries = self._validated_manifest_entries(ref.identity, manifest)
        files: dict[str, bytes] = {}
        root = self._root(ref.identity)
        for entry in entries:
            path = entry["path"]
            result = self._immutable_objects.read_object(f"{root}/{path}")
            if result.status is ObjectReadStatus.FAILED:
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.READ_FAILED,
                    f"failed to read canonical file: {path}",
                )
            if result.status is ObjectReadStatus.NOT_FOUND or result.content is None:
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.NOT_READY,
                    f"canonical file is missing: {path}",
                )
            files[path] = result.content
        actual = CanonicalCenterVersion.from_files(ref.identity, files)
        if actual.manifest != manifest:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.CORRUPT_CONTENT,
                f"canonical version content does not match integrity manifest: {ref.locator}",
            )
        return actual

    def verify_version(self, ref: CanonicalCenterVersionRef) -> bool:
        try:
            self.read_version(ref)
            return True
        except CanonicalCenterStoreError as error:
            if error.code in {
                CanonicalCenterStoreErrorCode.NOT_READY,
                CanonicalCenterStoreErrorCode.CORRUPT_CONTENT,
            }:
                return False
            raise

    def _root(self, identity: CanonicalCenterVersionIdentity) -> str:
        return (
            f"{self._config.base_prefix}/{identity.skill_uuid}/"
            f"{identity.sc_version_number}"
        )

    def _control_root(self, identity: CanonicalCenterVersionIdentity) -> str:
        return (
            f"{self._config.control_prefix}/{identity.skill_uuid}/"
            f"{identity.sc_version_number}"
        )

    @staticmethod
    def _intent_body(version: CanonicalCenterVersion) -> bytes:
        return json.dumps(
            {
                "format_version": 1,
                "manifest_sha256": version.manifest_sha256,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def _read_required(self, key: str) -> bytes:
        result = self._immutable_objects.read_object(key)
        if result.status is ObjectReadStatus.FAILED:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.READ_FAILED,
                f"object storage read failed: {key}",
            )
        if result.status is ObjectReadStatus.NOT_FOUND or result.content is None:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.NOT_READY,
                f"required canonical object is missing: {key}",
            )
        return result.content

    def _read_manifest(
        self,
        ref: CanonicalCenterVersionRef,
        *,
        missing_is_error: bool,
    ) -> bytes | None:
        key = f"{self._control_root(ref.identity)}/content-manifest.json"
        result = self._immutable_objects.read_object(key)
        if result.status is ObjectReadStatus.FAILED:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.READ_FAILED,
                f"failed to read integrity manifest: {ref.locator}",
            )
        if result.status is ObjectReadStatus.NOT_FOUND or result.content is None:
            if missing_is_error:
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.NOT_READY,
                    f"canonical version content is incomplete: {ref.locator}",
                )
            return None
        return result.content

    @staticmethod
    def _validated_manifest_entries(
        identity: CanonicalCenterVersionIdentity,
        manifest: bytes,
    ) -> list[dict[str, Any]]:
        try:
            value = json.loads(manifest)
            entries = value["files"]
            if (
                not isinstance(entries, list)
                or value.get("format_version") != 1
                or value.get("skill_uuid") != identity.skill_uuid
                or value.get("sc_version_number") != identity.sc_version_number
            ):
                raise ValueError
            placeholders: dict[str, bytes] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError
                path = entry["path"]
                size = entry["size"]
                digest = entry["sha256"]
                if (
                    not isinstance(path, str)
                    or not isinstance(size, int)
                    or isinstance(size, bool)
                    or size < 0
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                    or path in placeholders
                ):
                    raise ValueError
                placeholders[path] = b"\0" if size else b""
            CanonicalCenterVersion.from_files(identity, placeholders)
            if len(entries) != len(placeholders):
                raise ValueError
        except (
            CanonicalCenterStoreError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.CORRUPT_CONTENT,
                "integrity manifest identity or file tree is invalid",
            ) from error
        return entries
