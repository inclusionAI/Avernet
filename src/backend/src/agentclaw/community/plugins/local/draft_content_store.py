"""In-memory Fake for the immutable Draft content-store contract."""

from __future__ import annotations

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStoreError,
    DraftContentStoreErrorCode,
    DraftRevisionIdentity,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.skill_package import ValidatedSkillPackage


class LocalDraftContentStore:
    """Deterministic Fake with the same exact-identity immutability semantics."""

    def __init__(self) -> None:
        self._revisions: dict[DraftRevisionRef, ValidatedSkillPackage] = {}

    def write_revision(
        self,
        identity: DraftRevisionIdentity,
        validated_package: ValidatedSkillPackage,
    ) -> DraftRevisionRef:
        ref = DraftRevisionRef.from_identity(identity)
        existing = self._revisions.get(ref)
        if existing is not None and existing != validated_package:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.CONTENT_CONFLICT,
                "Draft revision identity already contains different bytes",
            )
        self._revisions[ref] = validated_package
        return ref

    def read_revision(self, ref: DraftRevisionRef) -> ValidatedSkillPackage:
        try:
            return self._revisions[ref]
        except KeyError as exc:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.NOT_FOUND,
                "Draft revision was not found",
            ) from exc

    def delete_revision(self, ref: DraftRevisionRef) -> None:
        self._revisions.pop(ref, None)
