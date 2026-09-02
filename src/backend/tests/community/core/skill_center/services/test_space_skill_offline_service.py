"""Behavior tests for impact, Offline and transaction-race compensation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import ANY, MagicMock

import pytest

from agentclaw.community.api.space_skill_offline_service import (
    OfflineBlockerKind,
    OfflineImpactItem,
)
from agentclaw.community.api.service_artifact_lineage import (
    ServiceArtifactLineage,
    ServiceArtifactReference,
)
from agentclaw.community.core.service_bot.service_artifact_lineage_reader_protocol import (
    UnknownServiceArtifact,
)
from agentclaw.community.core.repository.space_skill_offline_types import (
    OfflineCommit,
    OfflineInspection,
    OfflineInstallationFact,
    OfflineMembershipFact,
    OfflinePublicationAttemptFact,
    OfflineSkillIdentity,
)
from agentclaw.community.core.skill_center.draft_content import DraftRevisionRef
from agentclaw.community.core.skill_center.errors import (
    SkillOfflineBlockedError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.skill_center.services.published_version_draft import (
    PreparedPublishedVersionDraft,
)
from agentclaw.community.core.skill_center.services.space_skill_offline_service import (
    SpaceSkillOfflineService,
)


_UUID = "11111111-1111-4111-8111-111111111111"
_EXISTING_REV = "22222222-2222-4222-8222-222222222222"
_NEW_REV = "33333333-3333-4333-8333-333333333333"


def _identity(*, offline=False, draft=False):
    return OfflineSkillIdentity(
        skill_id=51,
        skill_uuid=_UUID,
        name="draft-skill",
        sc_team_id=91,
        latest_version_id=61,
        latest_version_ordinal=2,
        sc_version_number="2.0.0",
        offline_at=datetime(2026, 8, 30) if offline else None,
        draft_target_version=3 if draft else None,
        draft_status="EDITING" if draft else None,
        draft_locator=(f"draft://{_UUID}/v3/{_EXISTING_REV}" if draft else None),
    )


def _inspection(
    *,
    identity=None,
    space_bound=True,
    actor_roles=("OWNER",),
    publication_attempts=(),
    memberships=(),
    installations=(),
):
    return OfflineInspection(
        identity=identity or _identity(),
        space_bound=space_bound,
        actor_roles=actor_roles,
        publication_attempts=publication_attempts,
        memberships=memberships,
        installations=installations,
    )


def _service(*, inspection, lineage_result=None):
    access = MagicMock()
    repository = MagicMock()
    repository.inspect.return_value = inspection
    lineage = MagicMock()
    lineage.scan.return_value = lineage_result or ServiceArtifactLineage((), ())
    drafts = MagicMock()
    prepared = PreparedPublishedVersionDraft(
        expected_version_id=61,
        target_version=3,
        description="published",
        ref=DraftRevisionRef(
            tenant="tenant-a",
            env="test",
            skill_uuid=_UUID,
            target_version=3,
            revision_id=_NEW_REV,
        ),
    )
    drafts.prepare.return_value = prepared
    repository.commit.return_value = OfflineCommit(
        changed=True,
        target_version=3,
        status="EDITING",
        locator=prepared.ref.locator,
    )
    service = SpaceSkillOfflineService(
        access=access,
        repository=repository,
        lineage=lineage,
        drafts=drafts,
        env_provider=lambda: "test",
        tenant_provider=lambda: "tenant-a",
    )
    return service, access, repository, lineage, drafts, prepared


def test_impact_counts_explicit_blockers_then_returns_unknown_artifact_as_warning():
    inspection = _inspection(
        identity=_identity(draft=True),
        publication_attempts=(
            OfflinePublicationAttemptFact(
                id=2,
                target_version_ordinal=3,
                status="MATERIALIZING",
            ),
        ),
        memberships=(OfflineMembershipFact(id=3, skill_set_name="Set"),),
        installations=(OfflineInstallationFact(id=4, bot_id="Bot"),),
    )
    service, access, *_ = _service(
        inspection=inspection,
        lineage_result=ServiceArtifactLineage(
            references=(
                ServiceArtifactReference(
                    publish_id=5,
                    source_bot_id="service-5",
                    source_bot_name="Service",
                    service_version=1,
                    sc_version_number="2.0.0",
                ),
            ),
            unknown=(
                UnknownServiceArtifact(
                    resource_id="6",
                    display_name="Unknown artifact",
                ),
            ),
        ),
    )

    impact = service.impact(
        space_id=7, skill_id=51, actor_id="owner-1", page=2, page_size=2
    )

    assert impact.blocked is True
    assert impact.total == 5
    assert impact.counts == {
        kind.value: 1
        for kind in OfflineBlockerKind
        if kind is not OfflineBlockerKind.UNKNOWN_ARTIFACT
    }
    assert [item.kind for item in impact.items] == [
        OfflineBlockerKind.MEMBERSHIP,
        OfflineBlockerKind.INSTALLATION,
    ]
    assert impact.warnings == (
        OfflineImpactItem(
            kind=OfflineBlockerKind.UNKNOWN_ARTIFACT,
            resource_id="6",
            display_name="Unknown artifact",
        ),
    )
    access.require_space_member.assert_called_once_with(
        space_id=7, user_id="owner-1"
    )


def test_offline_prepares_exact_revision_then_commits_and_returns_vn_plus_one():
    service, _access, repository, _lineage, drafts, prepared = _service(
        inspection=_inspection()
    )

    result = service.offline(space_id=7, skill_id=51, actor_id="owner-1")

    assert result.changed is True
    assert result.lifecycle_status == "OFFLINE"
    assert result.draft.target_version == 3
    assert result.draft.revision_id == _NEW_REV
    drafts.prepare.assert_called_once()
    repository.commit.assert_called_once_with(
        space_id=7,
        skill_id=51,
        actor_id="owner-1",
        expected_version_id=61,
        target_version=3,
        new_locator=prepared.ref.locator,
        new_description="published",
        env="test",
        guard=ANY,
    )


def test_offline_idempotent_replay_does_not_prepare_another_revision():
    service, _access, repository, _lineage, drafts, _prepared = _service(
        inspection=_inspection(identity=_identity(offline=True, draft=True))
    )

    result = service.offline(space_id=7, skill_id=51, actor_id="owner-1")

    assert result.changed is False
    assert result.draft.revision_id == _EXISTING_REV
    drafts.prepare.assert_not_called()
    repository.commit.assert_not_called()


def test_unknown_artifact_warning_does_not_block_offline():
    service, _access, repository, _lineage, _drafts, _prepared = _service(
        inspection=_inspection(),
        lineage_result=ServiceArtifactLineage(
            references=(),
            unknown=(
                UnknownServiceArtifact(
                    resource_id="artifact-scan",
                    display_name="Service Artifact lineage is unreadable",
                ),
            ),
        ),
    )

    result = service.offline(space_id=7, skill_id=51, actor_id="owner-1")

    assert result.changed is True
    repository.commit.assert_called_once()


def test_transaction_recheck_unknown_artifact_warning_does_not_roll_back_offline():
    service, _access, repository, lineage, _drafts, _prepared = _service(
        inspection=_inspection()
    )
    lineage.scan.side_effect = [
        ServiceArtifactLineage((), ()),
        ServiceArtifactLineage(
            (),
            (
                UnknownServiceArtifact(
                    resource_id="artifact-scan",
                    display_name="Service Artifact lineage is unreadable",
                ),
            ),
        ),
    ]

    def _commit(**kwargs):
        kwargs["guard"](_inspection())
        return OfflineCommit(
            changed=True,
            target_version=3,
            status="EDITING",
            locator=kwargs["new_locator"],
        )

    repository.commit.side_effect = _commit

    result = service.offline(space_id=7, skill_id=51, actor_id="owner-1")

    assert result.changed is True


def test_concurrent_blocker_from_transaction_recheck_discards_prepared_revision():
    service, _access, repository, lineage, drafts, prepared = _service(
        inspection=_inspection()
    )
    latest = OfflineImpactItem(
        kind=OfflineBlockerKind.SERVICE_ARTIFACT,
        resource_id="88",
        display_name="Service 88 V4 (Skill 2.0.0)",
    )
    lineage.scan.side_effect = [
        ServiceArtifactLineage((), ()),
        ServiceArtifactLineage(
            (
                ServiceArtifactReference(
                    publish_id=88,
                    source_bot_id="service-88",
                    source_bot_name="Service 88",
                    service_version=4,
                    sc_version_number="2.0.0",
                ),
            ),
            (),
        )
    ]

    def _commit(**kwargs):
        kwargs["guard"](_inspection())

    repository.commit.side_effect = _commit

    with pytest.raises(SkillOfflineBlockedError) as blocked:
        service.offline(space_id=7, skill_id=51, actor_id="owner-1")

    assert blocked.value.impact.items == (latest,)
    drafts.discard.assert_called_once_with(prepared)


@pytest.mark.parametrize(
    ("space_bound", "actor_roles"),
    [(False, ("OWNER",)), (True, ()), (True, ("MEMBER",))],
)
def test_owner_manager_policy_is_enforced_by_service(
    space_bound: bool, actor_roles: tuple[str, ...]
) -> None:
    service, _access, repository, lineage, drafts, _prepared = _service(
        inspection=_inspection(
            space_bound=space_bound,
            actor_roles=actor_roles,
        )
    )

    with pytest.raises(SpaceSkillGrantForbiddenError, match="owner or manager"):
        service.offline(space_id=7, skill_id=51, actor_id="member")

    repository.commit.assert_not_called()
    lineage.scan.assert_not_called()
    drafts.prepare.assert_not_called()
