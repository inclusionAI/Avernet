"""Recoverable Offline orchestration at the Space Skill seam."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentclaw.community.core.service_bot.service_artifact_lineage_reader_protocol import (
    ServiceArtifactLineageReaderProtocol,
)
from agentclaw.community.core.skill_center.space_skill_offline_service_protocol import (
    OfflineBlockerKind,
    OfflineImpact,
    OfflineImpactItem,
    SpaceSkillOfflineResult,
    SpaceSkillOfflineServiceProtocol,
)
from agentclaw.community.core.repository.space_skill_offline_types import (
    OfflineInspection,
    OfflineSkillIdentity,
)
from agentclaw.community.core.repository.protocols.space_skill_offline import (
    SpaceSkillOfflineRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    SkillOfflineBlockedError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol


_BLOCKER_ORDER = {kind: index for index, kind in enumerate(OfflineBlockerKind)}


@dataclass(frozen=True, slots=True)
class _OfflineEvaluation:
    identity: OfflineSkillIdentity
    blockers: tuple[OfflineImpactItem, ...]
    warnings: tuple[OfflineImpactItem, ...] = ()


class SpaceSkillOfflineService(SpaceSkillOfflineServiceProtocol):
    def __init__(
        self,
        *,
        access: SpaceAccessServiceProtocol,
        repository: SpaceSkillOfflineRepositoryProtocol,
        lineage: ServiceArtifactLineageReaderProtocol,
        env_provider: Callable[[], str],
        drafts=None,
        tenant_provider: Callable[[], str] | None = None,
    ) -> None:
        self._access = access
        self._repository = repository
        self._lineage = lineage
        self._env_provider = env_provider

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
        if identity.offline_at is not None:
            return self._result(changed=False, offline_at=identity.offline_at)
        if inspection.blockers:
            raise SkillOfflineBlockedError(
                self._impact(inspection, page=1, page_size=20)
            )

        committed = self._repository.commit(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
            guard=self._guard_locked_offline,
        )
        return self._result(changed=committed.changed, offline_at=committed.offline_at)

    def _inspect(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> _OfflineEvaluation:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        inspection = self._repository.inspect(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )
        return self._evaluate(inspection)

    def _guard_locked_offline(self, inspection: OfflineInspection) -> None:
        latest = self._evaluate(inspection)
        if latest.blockers:
            raise SkillOfflineBlockedError(
                self._impact(latest, page=1, page_size=20)
            )

    def _evaluate(self, inspection: OfflineInspection) -> _OfflineEvaluation:
        if not inspection.space_bound or not {
            "OWNER",
            "MANAGER",
        }.intersection(inspection.actor_roles):
            raise SpaceSkillGrantForbiddenError("owner or manager required")
        identity = inspection.identity
        blockers: list[OfflineImpactItem] = []
        if identity.draft_status is not None:
            blockers.append(
                OfflineImpactItem(
                    kind=OfflineBlockerKind.DRAFT,
                    resource_id=str(identity.skill_id),
                    display_name=f"Draft V{identity.draft_target_version}",
                )
            )
        blockers.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.PUBLICATION,
                resource_id=str(attempt.id),
                display_name=f"Publication V{attempt.target_version_ordinal}",
            )
            for attempt in inspection.publication_attempts
        )
        blockers.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.MEMBERSHIP,
                resource_id=str(membership.id),
                display_name=membership.skill_set_name,
            )
            for membership in inspection.memberships
        )
        blockers.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.INSTALLATION,
                resource_id=str(installation.id),
                display_name=installation.bot_id,
            )
            for installation in inspection.installations
        )
        return self._with_artifact_blockers(
            _OfflineEvaluation(identity=identity, blockers=tuple(blockers))
        )

    def _with_artifact_blockers(
        self, evaluation: _OfflineEvaluation
    ) -> _OfflineEvaluation:
        lineage = self._lineage.scan(
            skill_uuid=evaluation.identity.skill_uuid,
            env=self._env_provider(),
        )
        blockers = list(evaluation.blockers)
        blockers.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.SERVICE_ARTIFACT,
                resource_id=str(reference.publish_id),
                display_name=(
                    f"{reference.source_bot_name} V{reference.service_version} "
                    f"(Skill {reference.sc_version_number})"
                ),
            )
            for reference in lineage.references
        )
        warnings = list(evaluation.warnings)
        warnings.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.UNKNOWN_ARTIFACT,
                resource_id=item.resource_id,
                display_name=item.display_name,
            )
            for item in lineage.unknown
        )
        ordered = tuple(
            sorted(
                blockers,
                key=lambda item: (
                    _BLOCKER_ORDER[item.kind],
                    item.resource_id,
                    item.display_name,
                ),
            )
        )
        ordered_warnings = tuple(
            sorted(
                warnings,
                key=lambda item: (
                    _BLOCKER_ORDER[item.kind],
                    item.resource_id,
                    item.display_name,
                ),
            )
        )
        return _OfflineEvaluation(
            identity=evaluation.identity,
            blockers=ordered,
            warnings=ordered_warnings,
        )

    @staticmethod
    def _impact(
        inspection: _OfflineEvaluation, *, page: int, page_size: int
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
            warnings=inspection.warnings,
        )

    def _result(self, *, changed: bool, offline_at) -> SpaceSkillOfflineResult:
        return SpaceSkillOfflineResult(
            changed=changed,
            lifecycle_status="OFFLINE",
            offline_at=offline_at,
        )


__all__ = ["SpaceSkillOfflineService"]
