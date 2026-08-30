"""Object-storage adapter for immutable Draft revision ZIPs."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStoreConfig,
    DraftContentStoreError,
    DraftContentStoreErrorCode,
    DraftRevisionIdentity,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    SkillPackageValidator,
    ValidatedSkillPackage,
)
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectCreateResult,
    ObjectReadStatus,
)


@runtime_checkable
class _DraftRevisionObjectStorage(ImmutableObjectStorageCapability, Protocol):
    """Immutable object operations plus idempotent deletion for Draft ZIPs."""

    def delete_object(self, key: str) -> bool: ...


class OssDraftContentStore:
    """Store one verified canonical ZIP at each exact Draft revision key."""

    def __init__(
        self,
        *,
        object_storage: _DraftRevisionObjectStorage,
        package_validator: SkillPackageValidator,
        config: DraftContentStoreConfig,
    ) -> None:
        if not isinstance(object_storage, _DraftRevisionObjectStorage):
            raise ValueError(
                "DraftContentStore requires atomic create, three-state read, "
                "and idempotent delete object storage capabilities"
            )
        self._objects = object_storage
        self._validator = package_validator
        self._base_prefix_template = config.base_prefix_template

    def write_revision(
        self,
        identity: DraftRevisionIdentity,
        validated_package: ValidatedSkillPackage,
    ) -> DraftRevisionRef:
        try:
            package = self._validator.revalidate(validated_package)
        except (SkillPackageInvalidError, SkillPackageTooLargeError) as exc:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.CORRUPT_CONTENT,
                "Draft revision package is not a consistent validated value",
            ) from exc
        ref = DraftRevisionRef.from_identity(identity)
        key = self._object_key(ref)
        created = self._objects.create_object_if_absent(key, package.canonical_zip)
        if created is ObjectCreateResult.FAILED:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.WRITE_FAILED,
                "Draft revision write failed",
            )
        verified = self._objects.read_object(key)
        if verified.status is ObjectReadStatus.FAILED:
            raise DraftContentStoreError(
                (
                    DraftContentStoreErrorCode.WRITE_FAILED
                    if created is ObjectCreateResult.CREATED
                    else DraftContentStoreErrorCode.READ_FAILED
                ),
                "Draft revision could not be read after conditional create",
            )
        if verified.status is ObjectReadStatus.NOT_FOUND:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.WRITE_FAILED,
                "Draft revision disappeared after conditional create",
            )
        if verified.content is None:
            raise DraftContentStoreError(
                (
                    DraftContentStoreErrorCode.WRITE_FAILED
                    if created is ObjectCreateResult.CREATED
                    else DraftContentStoreErrorCode.READ_FAILED
                ),
                "Object storage returned FOUND without Draft content",
            )
        if verified.content != package.canonical_zip:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.CONTENT_CONFLICT,
                "Draft revision identity already contains different bytes",
            )
        return ref

    def read_revision(self, ref: DraftRevisionRef) -> ValidatedSkillPackage:
        ref = DraftRevisionRef.from_identity(ref)
        result = self._objects.read_object(self._object_key(ref))
        if result.status is ObjectReadStatus.NOT_FOUND:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.NOT_FOUND,
                "Draft revision was not found",
            )
        if result.status is ObjectReadStatus.FAILED:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.READ_FAILED,
                "Draft revision read failed",
            )
        stored = result.content
        if stored is None:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.READ_FAILED,
                "Object storage returned FOUND without Draft content",
            )
        try:
            package = self._validator.validate_zip(stored)
        except (SkillPackageInvalidError, SkillPackageTooLargeError) as exc:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.CORRUPT_CONTENT,
                "Stored Draft revision is not a valid package",
            ) from exc
        if package.canonical_zip != stored:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.CORRUPT_CONTENT,
                "Stored Draft revision is not a canonical ZIP",
            )
        return package

    def delete_revision(self, ref: DraftRevisionRef) -> None:
        ref = DraftRevisionRef.from_identity(ref)
        if not self._objects.delete_object(self._object_key(ref)):
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.DELETE_FAILED,
                "Draft revision delete failed",
            )

    def _object_key(self, ref: DraftRevisionRef) -> str:
        base = self._base_prefix_template.format(env=ref.env)
        return (
            f"{base}/{ref.tenant}/{ref.env}/{ref.skill_uuid}/"
            f"v{ref.target_version}/revisions/{ref.revision_id}.zip"
        )
