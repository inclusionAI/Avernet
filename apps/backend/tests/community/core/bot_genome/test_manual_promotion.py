"""Audited human authorization delivers candidates, without fabricating gate acceptance."""

import pytest
import pytest_asyncio
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_genome.errors import (
    Drifted,
    NotPromotable,
    RefConflict,
)
from agentclaw.community.core.bot_genome.models import (
    PromotionStatus,
    RevisionStatus,
    RunScope,
)
from agentclaw.community.core.bot_genome.promotion.promotion_service_protocol import (
    ManualPromotionAuthorization,
)

from tests.community.core.bot_genome.example_blobs import (
    SUPPORT_BOT,
    SUPPORT_BOT_CANDIDATE,
    load_patch,
    spec_of,
)
from tests.community.core.bot_genome.service_fixtures import BOT, OWNER, RUN, World
from tests.community.core.bot_genome.test_capture import FakeReader


@pytest_asyncio.fixture
async def prepared(tmp_path):
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    first = await world.registry.capture(BOT, created_by=OWNER)
    parent = first.revision.revision.id
    await world.promote(parent, expected=None, actor=OWNER)
    candidate = await world.registry.commit_patch(
        BOT,
        load_patch("01-file-edit.json"),
        created_by=RUN,
        status=RevisionStatus.CANDIDATE,
        run=RunScope(RUN.run_id, RUN.actor, ("persona", "skills", "resources")),
    )
    meta = candidate.revision.revision
    auth = ManualPromotionAuthorization(
        id="a" * 32,
        candidate_id=meta.patch_digest,
        run_id=RUN.run_id,
        revision_id=meta.id,
        parent_revision_id=parent,
        strategy=RUN.actor,
        user_id=OWNER.actor,
        reason="Human inspected the immutable diff; testing promotion",
        authorized_at="2026-11-26T00:00:00Z",
    )
    return world, auth


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,status",
    [
        (ApplyStatus.SUCCEEDED, PromotionStatus.APPLIED),
        (ApplyStatus.PARTIAL, PromotionStatus.APPLIED_PARTIAL),
        (ApplyStatus.FAILED, PromotionStatus.APPLY_FAILED),
    ],
)
async def test_manual_authorization_only_settles_refs_at_apply_end(
    prepared, outcome, status
):
    world, auth = prepared
    before = {r.name: r.revision for r in world.registry.list_refs(BOT)}
    with pytest.raises(NotPromotable):
        await world.promotion.promote(
            BOT,
            revision=auth.revision_id,
            ref="active",
            expected_revision=auth.parent_revision_id,
            actor=OWNER,
            reason="not authorized",
        )
    promotion = await world.promotion.promote_manual(BOT, auth)
    assert promotion.status is PromotionStatus.APPLYING
    assert promotion.actor == OWNER
    assert (
        world.registry.get_revision(BOT, auth.revision_id).revision.status
        is RevisionStatus.CANDIDATE
    )
    assert {r.name: r.revision for r in world.registry.list_refs(BOT)} == before
    row = world.promotions.get(
        env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, promotion_id=auth.id
    )
    assert ManualPromotionAuthorization.model_validate_json(row.authorization) == auth
    settled = world.deliver(promotion, outcome)
    assert settled.status is status
    refs = {r.name: r.revision for r in world.registry.list_refs(BOT)}
    if outcome is ApplyStatus.FAILED:
        assert refs == before
        assert (
            world.registry.get_revision(BOT, auth.revision_id).revision.status
            is RevisionStatus.CANDIDATE
        )
    else:
        assert (
            refs["active"] == auth.revision_id
            and refs["previous"] == auth.parent_revision_id
        )
        assert (
            world.registry.get_revision(BOT, auth.revision_id).revision.status
            is RevisionStatus.PROMOTED
        )
    starts = len(world.applier.starts)
    assert await world.promotion.promote_manual(BOT, auth) == settled
    assert len(world.applier.starts) == starts, (
        "lost response must recover, never start a second Apply"
    )


@pytest.mark.asyncio
async def test_failed_authorization_replays_do_not_restart(prepared):
    world, auth = prepared
    world.applier.fail = RuntimeError("private upstream detail")
    failed = await world.promotion.promote_manual(BOT, auth)
    assert failed.status is PromotionStatus.APPLY_FAILED
    assert "private upstream" not in failed.apply_detail
    assert await world.promotion.promote_manual(BOT, auth) == failed
    assert len(world.applier.starts) == 2  # includes baseline
    with pytest.raises(NotPromotable):
        await world.promotion.promote_manual(
            BOT, auth.model_copy(update={"reason": "changed body"})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other-run"),
        ("strategy", "other@1"),
        ("candidate_id", "sha256:" + "0" * 64),
    ],
)
async def test_manual_authorization_must_match_candidate_provenance(
    prepared, field, value
):
    world, auth = prepared
    with pytest.raises(NotPromotable):
        await world.promotion.promote_manual(
            BOT, auth.model_copy(update={field: value})
        )
    assert len(world.applier.starts) == 1


@pytest.mark.asyncio
async def test_manual_does_not_inherit_owner_drift_override(prepared):
    world, auth = prepared
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT_CANDIDATE))
    with pytest.raises(Drifted):
        await world.promotion.promote_manual(BOT, auth)
    assert len(world.applier.starts) == 1


@pytest.mark.asyncio
async def test_stale_parent_refuses_before_delivery(prepared):
    world, auth = prepared

    # Fake preflight simulates another completed promotion between drift read and CAS.
    def move():
        with world.db.orm_session() as session:
            from agentclaw.community.core.bot_genome.repository.models import (
                GenomeRefModel,
            )
            from sqlalchemy import update

            session.execute(
                update(GenomeRefModel)
                .where(GenomeRefModel.name == "active")
                .values(revision_id="sha256:" + "f" * 64)
            )

    world.applier.on_preflight = move
    with pytest.raises(RefConflict):
        await world.promotion.promote_manual(BOT, auth)
    assert len(world.applier.starts) == 1


@pytest.mark.asyncio
async def test_pending_claim_survives_service_restart_without_starting_apply(prepared):
    from datetime import datetime, timezone

    from agentclaw.community.core.bot_genome.promotion.service import (
        GenomePromotionService,
    )
    from agentclaw.community.core.bot_genome.repository.models import (
        GenomePromotionRow,
    )

    world, auth = prepared
    # A process died after its durable claim, before it could record an Apply ID.
    row = GenomePromotionRow(
        env="dev",
        entity_id=BOT.entity_id,
        bot_id=BOT.bot_id,
        promotion_id=auth.id,
        ref="active",
        revision_id=auth.revision_id,
        from_revision_id=auth.parent_revision_id,
        actor_kind=OWNER.kind.value,
        actor_id=OWNER.audit,
        reason=auth.reason,
        authorization=auth.model_dump_json(),
        status=PromotionStatus.PENDING.value,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    _, claimed = world.promotions.insert_once(row)
    assert claimed
    persisted, claimed_again = world.promotions.insert_once(row)
    assert not claimed_again and persisted.authorization == row.authorization
    restarted = GenomePromotionService(
        registry=world.registry,
        refs=world.refs,
        promotions=world.promotions,
        applier=world.applier,
        env=lambda: "dev",
    )
    before = tuple(world.registry.list_refs(BOT))
    receipt = await restarted.promote_manual(BOT, auth)
    assert receipt.status is PromotionStatus.PENDING and receipt.apply_id is None
    assert (
        len(world.applier.starts) == 1
    )  # baseline only; uncertainty is not permission to replay
    assert tuple(world.registry.list_refs(BOT)) == before
    with pytest.raises(NotPromotable):
        await restarted.promote_manual(
            BOT, auth.model_copy(update={"reason": "changed"})
        )
