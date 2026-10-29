"""Rejected rollback admission never leaves half of the publication pair flipped."""

import pytest
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.core.service_bot.repository.models import PublishStatus

from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    publication as _publication,
)

publication = _publication


@pytest.mark.parametrize("rejected", ["demoted", "restored"])
def test_rejected_rollback_pair_is_fully_unchanged(publication, rejected):
    db, repo, record = publication
    # Seed the historical native state; this test exercises rollback admission.
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(id=record.id).update({"status": PublishStatus.SUCCESS})
    current = repo.get_by_id(record.id)
    target = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.old",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.UPGRADED,
            version=0,
            env="dev",
            last_pub_id=0,
            ext={"old": True},
        )
    )
    result = repo.rollback_flip(
        demoted_publish_id=current.id,
        demoted_ext={"changed": True},
        demoted_from_status=PublishStatus.FAILED
        if rejected == "demoted"
        else PublishStatus.SUCCESS,
        demoted_to_status=PublishStatus.DRAFT,
        restored_publish_id=target.id,
        restored_ext={"changed": True},
        restored_from_status=PublishStatus.FAILED
        if rejected == "restored"
        else PublishStatus.UPGRADED,
        restored_to_status=PublishStatus.SUCCESS,
    )
    assert result == (False, False)
    assert repo.get_by_id(current.id).status == current.status
    assert repo.get_by_id(current.id).ext == current.ext
    assert repo.get_by_id(target.id).status == target.status
    assert repo.get_by_id(target.id).ext == target.ext
