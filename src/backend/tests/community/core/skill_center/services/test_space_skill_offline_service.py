"""Behavior tests for impact, Offline and transaction-race compensation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.space_skill_offline_service import (
    OfflineBlockerKind,
    OfflineImpactItem,
)
from agentclaw.community.core.repository.space_skill_offline_types import (
    OfflineCommit,
    OfflineInspection,
    OfflineSkillIdentity,
)
from agentclaw.community.core.skill_center.draft_content import DraftRevisionRef
from agentclaw.community.core.skill_center.errors import SkillOfflineBlockedError
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


def _service(*, inspection):
    access = MagicMock()
    repository = MagicMock()
    repository.inspect.return_value = inspection
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
        drafts=drafts,
        env_provider=lambda: "test",
        tenant_provider=lambda: "tenant-a",
    )
    return service, access, repository, drafts, prepared


def test_impact_counts_all_blocker_kinds_then_paginates_items():
    blockers = tuple(
        OfflineImpactItem(kind=kind, resource_id=str(index), display_name=kind.value)
        for index, kind in enumerate(
            (
                OfflineBlockerKind.DRAFT,
                OfflineBlockerKind.PUBLICATION,
                OfflineBlockerKind.MEMBERSHIP,
                OfflineBlockerKind.INSTALLATION,
                OfflineBlockerKind.SERVICE_ARTIFACT,
                OfflineBlockerKind.UNKNOWN_ARTIFACT,
            ),
            start=1,
        )
    )
    service, access, *_ = _service(
        inspection=OfflineInspection(identity=_identity(), blockers=blockers)
    )

    impact = service.impact(
        space_id=7, skill_id=51, actor_id="owner-1", page=2, page_size=2
    )

    assert impact.blocked is True
    assert impact.total == 6
    assert impact.counts == {kind.value: 1 for kind in OfflineBlockerKind}
    assert [item.kind for item in impact.items] == [
        OfflineBlockerKind.MEMBERSHIP,
        OfflineBlockerKind.INSTALLATION,
    ]
    access.require_space_member.assert_called_once_with(
        space_id=7, user_id="owner-1"
    )


def test_offline_prepares_exact_revision_then_commits_and_returns_vn_plus_one():
    service, _access, repository, drafts, prepared = _service(
        inspection=OfflineInspection(identity=_identity(), blockers=())
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
    )


def test_offline_idempotent_replay_does_not_prepare_another_revision():
    service, _access, repository, drafts, _prepared = _service(
        inspection=OfflineInspection(identity=_identity(offline=True, draft=True), blockers=())
    )

    result = service.offline(space_id=7, skill_id=51, actor_id="owner-1")

    assert result.changed is False
    assert result.draft.revision_id == _EXISTING_REV
    drafts.prepare.assert_not_called()
    repository.commit.assert_not_called()


def test_concurrent_blocker_from_transaction_recheck_discards_prepared_revision():
    service, _access, repository, drafts, prepared = _service(
        inspection=OfflineInspection(identity=_identity(), blockers=())
    )
    latest = OfflineImpactItem(
        kind=OfflineBlockerKind.SERVICE_ARTIFACT,
        resource_id="88",
        display_name="Service 88 V4",
    )
    repository.commit.side_effect = SkillOfflineBlockedError(
        service._impact(
            OfflineInspection(identity=_identity(), blockers=(latest,)),
            page=1,
            page_size=20,
        )
    )

    with pytest.raises(SkillOfflineBlockedError) as blocked:
        service.offline(space_id=7, skill_id=51, actor_id="owner-1")

    assert blocked.value.impact.items == (latest,)
    drafts.discard.assert_called_once_with(prepared)
