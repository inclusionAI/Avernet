"""Object-storage adapter for immutable Draft revision ZIPs."""

from __future__ import annotations

import re
from string import Formatter

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
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


_SAFE_PREFIX_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class OssDraftContentStore:
    """Store one verified canonical ZIP at each exact Draft revision key."""

    def __init__(
        self,
        *,
        object_storage: ObjectStoragePlugin,
        package_validator: SkillPackageValidator,
        config: DraftContentStoreConfig,
    ) -> None:
        self._objects = object_storage
        self._validator = package_validator
        self._base_prefix_template = self._validate_base_prefix_template(
            config.base_prefix_template
        )

    def write_revision(
        self,
        identity: DraftRevisionIdentity,
        validated_package: ValidatedSkillPackage,
    ) -> DraftRevisionRef:
        ref = DraftRevisionRef.from_identity(identity)
        key = self._object_key(ref)
        existing = self._objects.get_object(key)
        if existing is not None:
            if existing == validated_package.canonical_zip:
                return ref
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.CONTENT_CONFLICT,
                "Draft revision identity already contains different bytes",
            )
        if not self._objects.put_object(key, validated_package.canonical_zip):
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.WRITE_FAILED,
                "Draft revision write failed",
            )
        verified = self._objects.get_object(key)
        if verified is None:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.WRITE_FAILED,
                "Draft revision could not be verified after write",
            )
        if verified != validated_package.canonical_zip:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.CONTENT_CONFLICT,
                "Draft revision changed while verifying the write",
            )
        return ref

    def read_revision(self, ref: DraftRevisionRef) -> ValidatedSkillPackage:
        stored = self._objects.get_object(self._object_key(ref))
        if stored is None:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.NOT_FOUND,
                "Draft revision was not found",
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

    @staticmethod
    def _validate_base_prefix_template(template: str) -> str:
        if not isinstance(template, str):
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix template must be a string",
            )
        try:
            parsed = list(Formatter().parse(template))
        except ValueError as exc:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix template is malformed",
            ) from exc
        fields = [
            (field_name, format_spec, conversion)
            for _literal, field_name, format_spec, conversion in parsed
            if field_name is not None
        ]
        if fields != [("env", "", None)]:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix template must contain one {env}",
            )
        rendered = template.format(env="validation")
        if (
            not rendered
            or rendered.startswith(("/", "\\"))
            or "\\" in rendered
            or any(
                part in {"", ".", ".."} or _SAFE_PREFIX_SEGMENT.fullmatch(part) is None
                for part in rendered.split("/")
            )
        ):
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix must be a safe relative path",
            )
        return template
