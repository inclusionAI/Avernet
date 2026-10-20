"""``GenomePromotionService``: the §7.1 order with a fake applier, drift, going back, the pending seam."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_genome.canonical import canonicalize
from agentclaw.community.core.bot_genome.errors import (
    Drifted,
    NotAppliable,
    NotPromotable,
    PromotionUnsupported,
    RefConflict,
    RevisionNotFound,
)
from agentclaw.community.core.bot_genome.models import BotRef, RevisionStatus
from agentclaw.community.core.bot_genome.promotion.applier import PendingDeliveryApplier
from tests.community.core.bot_genome.example_blobs import SUPPORT_BOT, BlobDir, load_patch, load_revisions
from tests.community.core.bot_genome.service_fixtures import BOT, OWNER, RUN, World
from tests.community.core.bot_genome.test_capture import FakeReader

RETURNS_MD = "sha256:a191ca8f49999530a2cf4f48562ffde7d62011b3bcfe040195da089a3f117c95"


@pytest.fixture
def world(tmp_path) -> World:
    w = World(tmp_path)
    w.reader = FakeReader.from_spec(load_revisions()[SUPPORT_BOT]["spec"])
    return w


async def _two_revisions(world: World) -> tuple[str, str]:
    """r1 captured, r2 an owner draft patch; returns their ids."""
    first = await world.registry.capture(BOT, created_by=OWNER)
    world.registry.put_content(BOT, BlobDir().read(RETURNS_MD), provenance=OWNER)
    second = await world.registry.record_patch(BOT, load_patch("00-mixed-r1-to-r2.json"), created_by=OWNER, status=RevisionStatus.DRAFT)
    return first.revision.revision.id, second.revision.revision.id


def _refs(world: World) -> dict[str, str]:
    return {r.name: r.revision for r in world.registry.list_refs(BOT)}


@pytest.mark.asyncio
async def test_promote_moves_the_pair_marks_promoted_records_the_document_and_starts_delivery(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    first = await world.promotion.promote(BOT, revision="r1", ref="active", expected_revision=None, actor=OWNER, reason="first")
    assert (first.revision, first.from_revision, first.status, first.apply_kind, first.apply_reference, first.apply_detail) == (r1, None, "applied", "fake", "apply_1", "started")
    assert first.going_back is False and first.actor == OWNER and first.reason == "first"
    assert _refs(world) == {"active": r1}
    assert world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.PROMOTED
    compiled = world.registry.compile(BOT, r1)
    assert first.document == compiled.document and first.document_digest == compiled.document_digest
    assert world.applier.preflights[0][1].document_digest == compiled.document_digest
    world.report(BOT, "apply_1")
    assert world.registry.applied_revision(BOT) == r1

    world.applier.next_reference = "apply_2"
    second = await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=r1, actor=RUN, reason="gate")
    assert second.from_revision == r1 and _refs(world) == {"active": r2, "previous": r1}
    page = world.promotion.list_promotions(BOT)
    assert page.total == 2 and [p.revision for p in page.items] == [r2, r1] and page.items[0].document is None
    assert world.promotion.get_promotion(BOT, second.id).document["manifest"]["resources"][-1]["path"] == "data/returns.md"
    with pytest.raises(RevisionNotFound):
        world.promotion.get_promotion(BOT, "x" * 32)


@pytest.mark.asyncio
async def test_going_back_is_a_promotion_of_a_promoted_revision(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="a")
    await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=r1, actor=OWNER, reason="b")
    back = await world.promotion.promote(BOT, revision="previous", ref="active", expected_revision=r2, actor=OWNER, reason="r2 misbehaves")
    assert back.revision == r1 and back.going_back is True and _refs(world) == {"active": r1, "previous": r2}
    assert world.registry.list_revisions(BOT).total == 2, "going back records no revision"


@pytest.mark.asyncio
async def test_refusals_before_the_move_leave_refs_and_statuses_untouched(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    candidate = await world.registry.record_patch(BOT, load_patch("01-file-edit.json"), created_by=RUN, status=RevisionStatus.CANDIDATE)
    with pytest.raises(NotPromotable):
        await world.promotion.promote(BOT, revision=candidate.revision.revision.id, ref="active", expected_revision=None, actor=OWNER, reason="x")
    with pytest.raises(RevisionNotFound):
        await world.promotion.promote(BOT, revision="r9", ref="active", expected_revision=None, actor=OWNER, reason="x")
    with pytest.raises(PromotionUnsupported):
        await world.promotion.promote(BOT, revision=r1, ref="canary", expected_revision=None, actor=OWNER, reason="x")
    world.applier.refuse = NotAppliable([])
    with pytest.raises(NotAppliable):
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    world.applier.refuse = None
    with pytest.raises(RefConflict) as caught:
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=r2, actor=OWNER, reason="x")
    assert caught.value.current is None
    assert _refs(world) == {} and world.promotion.list_promotions(BOT).total == 0
    assert world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.DRAFT
    assert world.applier.starts == []


@pytest.mark.asyncio
async def test_drift_refuses_automated_actors_only(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="a")
    world.report(BOT, "apply_by_hand")  # a direct PUT /config-manifest since the promotion
    with pytest.raises(Drifted) as caught:
        await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=r1, actor=RUN, reason="gate")
    assert (caught.value.active, caught.value.applied) == (r1, None)
    assert _refs(world) == {"active": r1}
    reconciled = await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=r1, actor=OWNER, reason="keep my edit")
    assert reconciled.revision == r2
    world.report(BOT, "apply_1")
    assert world.registry.applied_revision(BOT) == r2, "the apply the promotion started"
    await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=r2, actor=RUN, reason="rollback rule")


@pytest.mark.asyncio
async def test_a_failed_start_is_recorded_with_the_refs_moved(world: World) -> None:
    r1, _ = await _two_revisions(world)
    world.applier.fail = RuntimeError("device unreachable")
    failed = await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    assert (failed.status, failed.apply_reference) == ("apply_failed", None)
    assert "RuntimeError" in failed.apply_detail and "device unreachable" not in failed.apply_detail, "no exception text on the row"
    assert _refs(world) == {"active": r1} and world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.PROMOTED
    row = world.promotions.get(env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, promotion_id=failed.id)
    assert row.document_json == canonicalize(world.registry.compile(BOT, r1).document).decode()


@pytest.mark.asyncio
async def test_the_pending_seam_refuses_everything_before_any_ref_moves(world: World) -> None:
    r1, _ = await _two_revisions(world)
    world.promotion._applier = PendingDeliveryApplier()
    with pytest.raises(NotAppliable) as caught:
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    assert "RSI-04" in caught.value.reasons[0].message
    service_bot = BotRef(owner_id=BOT.owner_id, bot_id=BOT.bot_id, entity_id=BOT.entity_id, engine_type="openclaw", bot_type="service")
    with pytest.raises(PromotionUnsupported):
        await world.promotion.promote(service_bot, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    assert _refs(world) == {} and world.promotion.list_promotions(BOT).total == 0
    with pytest.raises(AssertionError):
        await PendingDeliveryApplier().start(BOT, world.registry.compile(BOT, r1), actor=OWNER, reason="x")


@pytest.mark.asyncio
async def test_re_promoting_the_current_active_keeps_previous(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="a")
    await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=r1, actor=OWNER, reason="b")
    again = await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=r2, actor=OWNER, reason="overwrite my direct edit")
    assert again.going_back is False and _refs(world) == {"active": r2, "previous": r1}
    assert len(world.refs.events(env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, name="active")) == 3


@pytest.mark.asyncio
async def test_a_write_failure_after_the_move_rolls_the_move_back(world: World, monkeypatch) -> None:
    r1, _ = await _two_revisions(world)
    from agentclaw.community.core.repository.implementations.bot import genome as repo_module

    def boom(*args, **kwargs):
        raise RuntimeError("row insert failed")

    monkeypatch.setattr(repo_module, "GenomePromotionModel", boom)
    with pytest.raises(RuntimeError):
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    assert _refs(world) == {} and world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.DRAFT
    assert world.applier.starts == []
