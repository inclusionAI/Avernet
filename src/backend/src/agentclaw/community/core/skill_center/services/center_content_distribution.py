"""Prepare immutable exact-version ZIPs for Desktop filesystem Engines."""

from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentPackage,
    CenterContentPendingPackage,
    CenterContentReadyPackage,
    CenterContentURLSigner,
    CenterContentUnavailablePackage,
)
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectCreateResult,
    ObjectReadStatus,
    ObjectStoragePlugin,
)


_FORMAT_VERSION = 1
_SIGNED_URL_TTL_SECONDS = 3600
_DESCRIPTOR_FIELDS = frozenset(
    {
        "format_version",
        "skill_uuid",
        "sc_version_number",
        "package_sha256",
        "package_size",
        "package_key",
    }
)


class _ExactCreateStatus(StrEnum):
    READY = "READY"
    CONFLICT = "CONFLICT"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"


@dataclass(frozen=True, slots=True)
class CenterContentDistributionConfig:
    env: str = "prod"
    base_prefix_template: str = (
        "aidesktop/aidesktop_{env}/bolt_shared/skills-center-distribution/v1"
    )

    def __post_init__(self) -> None:
        try:
            rendered = self.base_prefix_template.format(env=self.env)
        except (AttributeError, KeyError, ValueError) as error:
            raise ValueError("invalid Center content distribution prefix") from error
        if (
            self.base_prefix_template.count("{env}") != 1
            or not self.env
            or self.env != self.env.strip()
            or "/" in self.env
            or "\\" in self.env
            or rendered.startswith("/")
            or "\\" in rendered
            or any(part in {"", ".", ".."} for part in rendered.split("/"))
        ):
            raise ValueError("invalid Center content distribution prefix")

    @property
    def base_prefix(self) -> str:
        return self.base_prefix_template.format(env=self.env).rstrip("/")


class CanonicalCenterContentDistribution:
    """Deep module hiding deterministic packaging and atomic publication."""

    def __init__(
        self,
        *,
        canonical_store: CanonicalCenterVersionStore,
        object_storage: ObjectStoragePlugin,
        url_signer: CenterContentURLSigner,
        config: CenterContentDistributionConfig,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(object_storage, ImmutableObjectStorageCapability):
            raise ValueError(
                "Center content distribution requires immutable object operations"
            )
        self._canonical_store = canonical_store
        self._objects = object_storage
        self._url_signer = url_signer
        self._config = config
        self._now = now
        self._prepare_locks: dict[tuple[str, str], threading.Lock] = {}
        self._prepare_locks_guard = threading.Lock()

    def lookup(
        self, identity: CanonicalCenterVersionIdentity
    ) -> CenterContentPackage:
        identity = CanonicalCenterVersionIdentity(
            identity.skill_uuid, identity.sc_version_number
        )
        descriptor = self._objects.read_object(self._descriptor_key(identity))
        if descriptor.status is ObjectReadStatus.NOT_FOUND:
            return CenterContentPendingPackage(identity)
        if descriptor.status is ObjectReadStatus.FAILED or descriptor.content is None:
            return CenterContentUnavailablePackage(
                identity,
                code="CENTER_CONTENT_LOOKUP_FAILED",
                retryable=True,
            )
        try:
            value = self._parse_descriptor(identity, descriptor.content)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return CenterContentUnavailablePackage(
                identity,
                code="CENTER_CONTENT_DESCRIPTOR_INVALID",
                retryable=False,
            )
        signed_url = self._url_signer.sign_url(
            str(value["package_key"]), _SIGNED_URL_TTL_SECONDS
        )
        if not isinstance(signed_url, str) or not signed_url:
            return CenterContentUnavailablePackage(
                identity,
                code="CENTER_CONTENT_SIGNING_FAILED",
                retryable=True,
            )
        expires_at = self._now().astimezone(UTC) + timedelta(
            seconds=_SIGNED_URL_TTL_SECONDS
        )
        return CenterContentReadyPackage(
            identity,
            package_sha256=str(value["package_sha256"]),
            package_size=int(value["package_size"]),
            signed_url=signed_url,
            expires_at=expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        )

    def prepare(
        self, identity: CanonicalCenterVersionIdentity
    ) -> CenterContentPackage:
        key = (identity.skill_uuid, identity.sc_version_number)
        with self._prepare_locks_guard:
            prepare_lock = self._prepare_locks.setdefault(key, threading.Lock())
        with prepare_lock:
            return self._prepare_locked(identity)

    def _prepare_locked(
        self, identity: CanonicalCenterVersionIdentity
    ) -> CenterContentPackage:
        existing = self.lookup(identity)
        if not isinstance(existing, CenterContentPendingPackage):
            return existing
        try:
            version = self._canonical_store.read_version(
                CanonicalCenterVersionRef(identity)
            )
        except CanonicalCenterStoreError as error:
            retryable = error.code in {
                CanonicalCenterStoreErrorCode.NOT_READY,
                CanonicalCenterStoreErrorCode.READ_FAILED,
            }
            return CenterContentUnavailablePackage(
                identity,
                code=f"CENTER_CONTENT_{error.code.value}",
                retryable=retryable,
            )
        package = self._archive(version)
        package_sha256 = hashlib.sha256(package).hexdigest()
        package_key = self._package_key(version, package_sha256)
        package_create = self._create_exact(package_key, package)
        if package_create is not _ExactCreateStatus.READY:
            return CenterContentUnavailablePackage(
                identity,
                code=(
                    "CENTER_CONTENT_PACKAGE_WRITE_FAILED"
                    if package_create is _ExactCreateStatus.RETRYABLE_FAILURE
                    else "CENTER_CONTENT_PACKAGE_CONFLICT"
                ),
                retryable=package_create is _ExactCreateStatus.RETRYABLE_FAILURE,
            )
        descriptor = json.dumps(
            {
                "format_version": _FORMAT_VERSION,
                "skill_uuid": identity.skill_uuid,
                "sc_version_number": identity.sc_version_number,
                "package_sha256": package_sha256,
                "package_size": len(package),
                "package_key": package_key,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        descriptor_create = self._create_exact(
            self._descriptor_key(identity), descriptor
        )
        if descriptor_create is not _ExactCreateStatus.READY:
            return CenterContentUnavailablePackage(
                identity,
                code=(
                    "CENTER_CONTENT_DESCRIPTOR_WRITE_FAILED"
                    if descriptor_create is _ExactCreateStatus.RETRYABLE_FAILURE
                    else "CENTER_CONTENT_DESCRIPTOR_CONFLICT"
                ),
                retryable=descriptor_create
                is _ExactCreateStatus.RETRYABLE_FAILURE,
            )
        return self.lookup(identity)

    def _create_exact(self, key: str, content: bytes) -> _ExactCreateStatus:
        result = self._objects.create_object_if_absent(key, content)
        if result is ObjectCreateResult.FAILED:
            return _ExactCreateStatus.RETRYABLE_FAILURE
        existing = self._objects.read_object(key)
        if existing.status is not ObjectReadStatus.FOUND:
            return _ExactCreateStatus.RETRYABLE_FAILURE
        if existing.content != content:
            return _ExactCreateStatus.CONFLICT
        return _ExactCreateStatus.READY

    def _descriptor_key(self, identity: CanonicalCenterVersionIdentity) -> str:
        return (
            f"{self._config.base_prefix}/{identity.skill_uuid}/"
            f"{identity.sc_version_number}/ready.json"
        )

    def _package_key(
        self, version: CanonicalCenterVersion, package_sha256: str
    ) -> str:
        identity = version.identity
        return (
            f"{self._config.base_prefix}/{identity.skill_uuid}/"
            f"{identity.sc_version_number}/{version.manifest_sha256}/"
            f"{package_sha256}.zip"
        )

    @staticmethod
    def _archive(version: CanonicalCenterVersion) -> bytes:
        target = io.BytesIO()
        with zipfile.ZipFile(
            target, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for item in version.files:
                info = zipfile.ZipInfo(item.path, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(
                    info,
                    item.content,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        return target.getvalue()

    def _parse_descriptor(
        self,
        identity: CanonicalCenterVersionIdentity,
        raw: bytes,
    ) -> dict[str, object]:
        value = json.loads(raw)
        if not isinstance(value, dict) or frozenset(value) != _DESCRIPTOR_FIELDS:
            raise ValueError("invalid descriptor shape")
        digest = value["package_sha256"]
        size = value["package_size"]
        key = value["package_key"]
        if (
            value["format_version"] != _FORMAT_VERSION
            or value["skill_uuid"] != identity.skill_uuid
            or value["sc_version_number"] != identity.sc_version_number
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(key, str)
            or PurePosixPath(key).is_absolute()
            or not key.startswith(
                f"{self._config.base_prefix}/{identity.skill_uuid}/"
                f"{identity.sc_version_number}/"
            )
            or not key.endswith(f"/{digest}.zip")
        ):
            raise ValueError("invalid descriptor values")
        return value


__all__ = [
    "CanonicalCenterContentDistribution",
    "CenterContentDistributionConfig",
]
