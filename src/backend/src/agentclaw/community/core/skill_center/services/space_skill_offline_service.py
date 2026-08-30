"""Recoverable Offline orchestration at the Space Skill seam."""

from __future__ import annotations

from collections.abc import Callable

from agentclaw.community.core.skill_center.space_skill_offline_service_protocol import (
    OfflineBlockerKind,
    OfflineDraft,
    OfflineImpact,
    SpaceSkillOfflineResult,
    SpaceSkillOfflineServiceProtocol,
)
from agentclaw.community.core.repository.space_skill_offline_types import (
    OfflineInspection,
)
from agentclaw.community.core.repository.protocols.space_skill_offline import (
    SpaceSkillOfflineRepositoryProtocol,
)
from agentclaw.community.core.skill_center.draft_content import DraftRevisionRef
from agentclaw.community.core.skill_center.errors import SkillOfflineBlockedError
from agentclaw.community.core.skill_center.services.published_version_draft import (
    PublishedVersionDraftBuilder,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol


class SpaceSkillOfflineService(SpaceSkillOfflineServiceProtocol):
    def __init__(
        self,
        *,
        access: SpaceAccessServiceProtocol,
        repository: SpaceSkillOfflineRepositoryProtocol,
        drafts: PublishedVersionDraftBuilder,
        env_provider: Callable[[], str],
        tenant_provider: Callable[[], str],
    ) -> None:
        self._access = access
        self._repository = repository
        self._drafts = drafts
        self._env_provider = env_provider
        self._tenant_provider = tenant_provider

    def impact(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        page: int,
        page_size: int,
    ) -> OfflineImpact:
        inspection = self._inspect(
            space_id=space_id, skill_id=skill_id, actor_id=actor_id
        )
        return self._impact(inspection, page=page, page_size=page_size)

    def offline(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillOfflineResult:
        inspection = self._inspect(
            space_id=space_id, skill_id=skill_id, actor_id=actor_id
        )
        identity = inspection.identity
        if (
            identity.offline_at is not None
            and identity.draft_status is not None
            and identity.draft_locator
            and identity.draft_target_version is not None
        ):
            return self._result(
                changed=False,
                target_version=identity.draft_target_version,
                status=identity.draft_status,
                locator=identity.draft_locator,
            )
        if inspection.blockers:
            raise SkillOfflineBlockedError(
                self._impact(inspection, page=1, page_size=20)
            )

        prepared = self._drafts.prepare(
            identity={
                "skill_uuid": identity.skill_uuid,
                "name": identity.name,
                "sc_team_id": identity.sc_team_id,
            },
            latest={
                "id": identity.latest_version_id,
                "version_ordinal": identity.latest_version_ordinal,
                "sc_version_number": identity.sc_version_number,
            },
        )
        try:
            committed = self._repository.commit(
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                expected_version_id=prepared.expected_version_id,
                target_version=prepared.target_version,
                new_locator=prepared.ref.locator,
                new_description=prepared.description,
                env=self._env_provider(),
            )
        except Exception:
            self._drafts.discard(prepared)
            raise
        if not committed.changed:
            self._drafts.discard(prepared)
        return self._result(
            changed=committed.changed,
            target_version=committed.target_version,
            status=committed.status,
            locator=committed.locator,
        )

    def _inspect(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> OfflineInspection:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.inspect(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )

    @staticmethod
    def _impact(
        inspection: OfflineInspection, *, page: int, page_size: int
    ) -> OfflineImpact:
        blockers = inspection.blockers
        counts = {kind.value: 0 for kind in OfflineBlockerKind}
        for item in blockers:
            counts[item.kind.value] += 1
        start = (page - 1) * page_size
        return OfflineImpact(
            blocked=bool(blockers),
            total=len(blockers),
            counts={kind: count for kind, count in counts.items() if count},
            items=blockers[start : start + page_size],
        )

    def _result(
        self, *, changed: bool, target_version: int, status: str, locator: str
    ) -> SpaceSkillOfflineResult:
        ref = DraftRevisionRef.from_locator(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            locator=locator,
        )
        return SpaceSkillOfflineResult(
            changed=changed,
            lifecycle_status="OFFLINE",
            draft=OfflineDraft(
                target_version=target_version,
                status=status,
                revision_id=ref.revision_id,
            ),
        )


__all__ = ["SpaceSkillOfflineService"]
