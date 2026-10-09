"""Prepare one immutable Draft revision from an exact Published Version."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
    DraftRevisionIdentity,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.errors import SkillNameChangedError
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownloadRequest,
    SkillCenterGateway,
    SkillCenterReadScope,
)
from agentclaw.community.plugin_api.space_skill_source import SpaceSkillSourcePlugin


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedPublishedVersionDraft:
    expected_version_id: int
    target_version: int
    description: str | None
    ref: DraftRevisionRef


class PublishedVersionDraftBuilder:
    """Hide exact Store repair, validation and immutable Revision creation."""

    def __init__(
        self,
        *,
        canonical_store: CanonicalCenterVersionStore,
        skill_center: SkillCenterGateway,
        sources: SpaceSkillSourcePlugin,
        validator: SkillPackageValidator,
        draft_store: DraftContentStore,
        env_provider: Callable[[], str],
        tenant_provider: Callable[[], str],
    ) -> None:
        self._canonical = canonical_store
        self._skill_center = skill_center
        self._sources = sources
        self._validator = validator
        self._draft_store = draft_store
        self._env_provider = env_provider
        self._tenant_provider = tenant_provider

    def prepare(self, *, identity, latest) -> PreparedPublishedVersionDraft:
        package = self.read_exact_package(identity=identity, version=latest)
        target_version = int(latest["version_ordinal"]) + 1
        revision = DraftRevisionIdentity(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            skill_uuid=identity["skill_uuid"],
            target_version=target_version,
            revision_id=str(uuid4()),
        )
        ref = self._draft_store.write_revision(revision, package)
        return PreparedPublishedVersionDraft(
            expected_version_id=int(latest["id"]),
            target_version=target_version,
            description=package.description,
            ref=ref,
        )

    def read_exact_package(self, *, identity, version):
        exact_identity = CanonicalCenterVersionIdentity(
            skill_uuid=identity["skill_uuid"],
            sc_version_number=version["sc_version_number"],
        )
        exact_ref = CanonicalCenterVersionRef(exact_identity)
        try:
            exact = self._canonical.read_version(exact_ref)
        except CanonicalCenterStoreError as exc:
            if exc.code not in {
                CanonicalCenterStoreErrorCode.NOT_READY,
                CanonicalCenterStoreErrorCode.CORRUPT_CONTENT,
            }:
                raise
            exact = self._repair(identity, exact_identity)
        package = self._validator.validate_directory(
            tuple((item.path, item.content) for item in exact.files)
        )
        if package.name != identity["name"]:
            raise SkillNameChangedError("SKILL.md name is immutable")
        return package

    def discard(self, prepared: PreparedPublishedVersionDraft) -> None:
        try:
            self._draft_store.delete_revision(prepared.ref)
        except Exception:
            logger.warning(
                "failed to clean prepared Published-Version Draft revision",
                exc_info=True,
            )

    def _repair(self, identity, exact_identity):
        if identity["sc_team_id"] is None:
            raise RuntimeError("Space Skill has no SkillCenter team identity")
        download = self._skill_center.get_exact_download(
            SkillCenterExactDownloadRequest(
                skill_code=identity["skill_uuid"],
                version_number=exact_identity.sc_version_number,
                scope=SkillCenterReadScope.TEAM,
                team_id=str(identity["sc_team_id"]),
            )
        )
        package = self._validator.validate_zip(
            self._sources.fetch_exact_package(
                url=download.download_url, expected_sha256=download.sha256
            )
        )
        version = CanonicalCenterVersion.from_files(exact_identity, dict(package.files))
        self._canonical.write_version(version)
        return self._canonical.read_version(CanonicalCenterVersionRef(exact_identity))


__all__ = ["PreparedPublishedVersionDraft", "PublishedVersionDraftBuilder"]
