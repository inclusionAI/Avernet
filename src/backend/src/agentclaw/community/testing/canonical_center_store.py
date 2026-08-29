"""Local in-memory fake for the Canonical Center Version Store."""

from __future__ import annotations

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersion,
    CanonicalCenterVersionRef,
)


class LocalCanonicalCenterVersionStore:
    def __init__(self) -> None:
        self._versions: dict[str, CanonicalCenterVersion] = {}

    def write_version(
        self, version: CanonicalCenterVersion
    ) -> CanonicalCenterVersionRef:
        ref = CanonicalCenterVersionRef(version.identity)
        existing = self._versions.get(ref.locator)
        if existing is None:
            self._versions[ref.locator] = version
            return ref
        if existing != version:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.CONTENT_CONFLICT,
                f"exact version identity already belongs to other content: {ref.locator}",
            )
        return ref

    def read_version(
        self, ref: CanonicalCenterVersionRef
    ) -> CanonicalCenterVersion:
        try:
            return self._versions[ref.locator]
        except KeyError as error:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.NOT_READY,
                f"canonical version is not Ready: {ref.locator}",
            ) from error

    def verify_version(self, ref: CanonicalCenterVersionRef) -> bool:
        return ref.locator in self._versions

