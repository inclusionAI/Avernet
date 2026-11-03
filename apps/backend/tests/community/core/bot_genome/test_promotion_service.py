"""``GenomePromotionService``: file, start the delivery, settle when it ends (fake applier, real settler)."""

from __future__ import annotations

import pytest

from datetime import timedelta

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_genome.errors import (
    ApplyBusy,
    Drifted,
    NotAppliable,
    NotPromotable,
    PromotionUnsupported,
    RefConflict,
    RevisionNotFound,
)
from agentclaw.community.core.bot_genome.models import (
    PromotionStatus,
    Provenance,
    ProvenanceKind,
    RevisionStatus,
    RunScope,
)
from tests.community.core.bot_genome.example_blobs import (
    SUPPORT_BOT,
    SUPPORT_BOT_CANDIDATE,
    BlobDir,
    load_patch,
    spec_of,
)
from tests.community.core.bot_genome.service_fixtures import BOT, OWNER, RUN, World
from tests.community.core.bot_genome.test_capture import FakeReader

RETURNS_MD = "sha256:a191ca8f49999530a2cf4f48562ffde7d62011b3bcfe040195da089a3f117c95"


@pytest.fixture
def world(tmp_path) -> World:
    w = World(tmp_path)
    w.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    return w


async def _two_revisions(world: World) -> tuple[str, str]:
    """r1 captured, r2 an owner draft patch; returns their ids."""
    first = await world.registry.capture(BOT, created_by=OWNER)
    world.registry.put_content(BOT, BlobDir().read(RETURNS_MD), provenance=OWNER)
    second = await world.registry.commit_patch(
        BOT, load_patch("00-mixed-r1-to-r2.json"), created_by=OWNER, status=RevisionStatus.DRAFT
    )
    return first.revision.revision.id, second.revision.revision.id


def _refs(world: World) -> dict[str, str]:
    return {r.name: r.revision for r in world.registry.list_refs(BOT)}


def _labels(world: World) -> dict[str, tuple[str, ...]]:
    return {r.name: r.labels for r in world.registry.list_refs(BOT)}


@pytest.mark.asyncio
async def test_promote_files_the_promotion_and_starts_its_delivery_without_moving_a_ref(world: World) -> None:
    r1, _ = await _two_revisions(world)
    first = await world.promotion.promote(
        BOT, revision="r1", ref="active", expected_revision=None, actor=OWNER, reason="first"
    )
    assert (first.revision, first.from_revision, first.status, first.apply_id) == (
        r1,
        None,
        PromotionStatus.APPLYING,
        "ap_1",
    )
    assert first.going_back is False and first.actor == OWNER and first.settled_at is None
    assert _refs(world) == {}, "the refs wait for the delivery to end"
    assert world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.DRAFT
    assert world.applier.preflights[0][1] == world.registry.compile(BOT, r1)
    assert world.applier.starts[0][1:] == (first.id, OWNER.actor, OWNER.audit)


@pytest.mark.asyncio
async def test_a_delivery_that_succeeds_moves_the_pair_and_marks_the_revision_promoted(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    first = await world.promote("r1", expected=None, actor=OWNER, reason="first")
    assert (first.status, first.apply_detail) == (PromotionStatus.APPLIED, "apply ap_1: applied")
    assert first.settled_at is not None and _refs(world) == {"active": r1} and _labels(world) == {"active": ()}
    assert world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.PROMOTED

    # An automated actor: the bot still reads as r1 (nothing moved it by hand), so no drift.
    second = await world.promote(r2, expected=r1, actor=RUN, reason="gate")
    assert second.from_revision == r1 and _refs(world) == {"active": r2, "previous": r1}
    page = world.promotion.list_promotions(BOT)
    assert page.total == 2 and [p.revision for p in page.items] == [r2, r1]
    assert world.promotion.get_promotion(BOT, second.id).revision == r2
    with pytest.raises(RevisionNotFound):
        world.promotion.get_promotion(BOT, "x" * 32)


@pytest.mark.asyncio
async def test_a_partial_delivery_moves_the_refs_and_labels_active_partial(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    partial = await world.promote(r1, expected=None, actor=OWNER, status=ApplyStatus.PARTIAL)
    assert partial.status == PromotionStatus.APPLIED_PARTIAL
    assert _refs(world) == {"active": r1} and _labels(world) == {"active": ("partial",)}
    await world.promote(r2, expected=r1, actor=OWNER)
    assert _labels(world) == {"active": (), "previous": ("partial",)}, (
        "the next complete delivery clears active's label; previous keeps the one it had as active"
    )


@pytest.mark.asyncio
async def test_a_failed_delivery_moves_nothing(world: World) -> None:
    r1, _ = await _two_revisions(world)
    failed = await world.promote(r1, expected=None, actor=OWNER, status=ApplyStatus.FAILED)
    assert failed.status == PromotionStatus.APPLY_FAILED and _refs(world) == {}
    assert world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.DRAFT
    again = world.deliver(failed, ApplyStatus.SUCCEEDED)
    assert again.status == PromotionStatus.APPLY_FAILED and _refs(world) == {}, "a settled promotion is final"


@pytest.mark.asyncio
async def test_active_moving_while_the_delivery_runs_moves_nothing(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    slow = await world.promotion.promote(
        BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="a"
    )
    fast = await world.promote(r2, expected=None, actor=OWNER)  # nothing stops a second promotion of a fake applier
    assert fast.status == PromotionStatus.APPLIED and _refs(world) == {"active": r2}
    settled = world.deliver(slow)
    assert settled.status == PromotionStatus.APPLY_FAILED and "no ref moved" in settled.apply_detail
    assert _refs(world) == {"active": r2}


@pytest.mark.asyncio
async def test_going_back_is_a_promotion_of_a_promoted_revision(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    await world.promote(r1, expected=None, actor=OWNER, reason="a")
    await world.promote(r2, expected=r1, actor=OWNER, reason="b")
    back = await world.promote("previous", expected=r2, actor=OWNER, reason="r2 misbehaves")
    assert back.revision == r1 and back.going_back is True and _refs(world) == {"active": r1, "previous": r2}
    assert world.registry.list_revisions(BOT).total == 2, "going back records no revision"


@pytest.mark.asyncio
async def test_refusals_before_the_promotion_is_filed_leave_nothing_behind(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    candidate = await world.registry.commit_patch(
        BOT,
        load_patch("01-file-edit.json"),
        created_by=RUN,
        status=RevisionStatus.CANDIDATE,
        run=RunScope("run_7f3", "clawevolve/bot-evolution@2.0.0", ("persona", "skills", "resources")),
    )
    with pytest.raises(NotPromotable):
        await world.promotion.promote(
            BOT, revision=candidate.revision.revision.id, ref="active", expected_revision=None, actor=OWNER, reason="x"
        )
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
    assert (
        _refs(world) == {candidate.ref: candidate.revision.revision.id}
        and world.promotion.list_promotions(BOT).total == 0
    )
    assert world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.DRAFT
    assert world.applier.starts == []


@pytest.mark.asyncio
async def test_drift_refuses_automated_actors_only(world: World) -> None:
    """Drift is read off the bot: after an owner edits the bot by hand (here,
    the script), its state no longer records as ``active``. A strategy run
    may not promote over that; the owner may, and once the bot holds what
    ``active`` says again, the run may move it."""
    r1, r2 = await _two_revisions(world)
    await world.promote(r1, expected=None, actor=OWNER, reason="a")
    world.reader._script = "#!/bin/sh\necho edited by hand\n"  # a direct edit since the promotion
    with pytest.raises(Drifted) as caught:
        await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=r1, actor=RUN, reason="gate")
    assert caught.value.active == r1 and caught.value.observed not in (r1, r2)
    assert _refs(world) == {"active": r1}
    reconciled = await world.promote(r2, expected=r1, actor=OWNER, reason="keep my edit")
    assert reconciled.revision == r2
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT_CANDIDATE))  # the delivery landed r2 on the bot
    assert await world.registry.drift(BOT) is None
    await world.promote(r1, expected=r2, actor=RUN, reason="rollback rule")


@pytest.mark.asyncio
async def test_a_delivery_that_does_not_start_is_recorded_failed_with_nothing_moved(world: World) -> None:
    r1, _ = await _two_revisions(world)
    world.applier.fail = RuntimeError("device unreachable")
    failed = await world.promotion.promote(
        BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x"
    )
    assert failed.status == PromotionStatus.APPLY_FAILED and failed.apply_id is None
    assert "RuntimeError" in failed.apply_detail and "device unreachable" not in failed.apply_detail, (
        "no exception text on the row"
    )
    assert _refs(world) == {} and world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.DRAFT
    row = world.promotions.get(env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, promotion_id=failed.id)
    assert row.status == "apply_failed" and row.apply_id is None


@pytest.mark.asyncio
async def test_a_busy_bot_refuses_the_promotion_and_records_why(world: World) -> None:
    r1, _ = await _two_revisions(world)
    world.applier.fail = ApplyBusy("another apply is running on bot b")
    with pytest.raises(ApplyBusy):
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    [recorded] = world.promotion.list_promotions(BOT).items
    assert recorded.status == PromotionStatus.APPLY_FAILED and "another apply" in recorded.apply_detail
    assert _refs(world) == {}


@pytest.mark.asyncio
async def test_active_moving_before_the_apply_takes_the_lock_refuses_the_promotion(world: World) -> None:
    """The compiler's ``check`` under the apply lock refuses a promotion another promotion overtook."""
    r1, _ = await _two_revisions(world)
    world.applier.fail = RefConflict("active", expected=None, current=r1)
    with pytest.raises(RefConflict):
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    [recorded] = world.promotion.list_promotions(BOT).items
    assert recorded.status == PromotionStatus.APPLY_FAILED and "active moved" in recorded.apply_detail
    assert _refs(world) == {}


@pytest.mark.asyncio
async def test_an_automated_actor_delivers_as_the_bot_owner_and_is_recorded_as_itself(world: World) -> None:
    r1, _ = await _two_revisions(world)
    promoted = await world.promotion.promote(
        BOT, revision=r1, ref="active", expected_revision=None, actor=RUN, reason="gate passed"
    )
    assert world.applier.starts[0][2:] == (BOT.owner_id, RUN.audit)
    settled = world.deliver(promoted)
    assert (settled.actor.kind, settled.actor.actor, settled.actor.run_id) == (
        ProvenanceKind.STRATEGY_RUN,
        RUN.actor,
        RUN.run_id,
    )
    moved_by = {r.name: r.moved_by for r in world.registry.list_refs(BOT)}
    assert moved_by["active"].run_id == RUN.run_id and moved_by["active"].as_dict()["run_id"] == RUN.run_id


@pytest.mark.asyncio
async def test_the_promotion_row_names_the_application_acting_for_the_user(world: World) -> None:
    r1, _ = await _two_revisions(world)
    delegated = Provenance(ProvenanceKind.USER, OWNER.actor, recorded_as="app:7:on-behalf-of:" + OWNER.actor)
    promoted = await world.promote(r1, expected=None, actor=delegated, reason="by an app")
    assert promoted.actor.actor == delegated.audit and _refs(world) == {"active": r1}
    assert world.applier.starts[0][2:] == (OWNER.actor, delegated.audit), "checked as the user, recorded as the app"


@pytest.mark.asyncio
async def test_a_status_change_during_the_delivery_is_a_refusal_not_an_overwrite(world: World) -> None:
    r1, _ = await _two_revisions(world)
    promoted = await world.promotion.promote(
        BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x"
    )
    world.registry.set_status(BOT, r1, RevisionStatus.REJECTED, actor=OWNER, reason="verification said no")
    settled = world.deliver(promoted)
    assert settled.status == PromotionStatus.APPLY_FAILED and _refs(world) == {}
    assert world.registry.get_revision(BOT, r1).revision.status == RevisionStatus.REJECTED, "the newer status wins"


@pytest.mark.asyncio
async def test_re_promoting_the_current_active_keeps_previous(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    await world.promote(r1, expected=None, actor=OWNER, reason="a")
    await world.promote(r2, expected=r1, actor=OWNER, reason="b")
    again = await world.promote(r2, expected=r2, actor=OWNER, reason="overwrite my direct edit")
    assert again.going_back is False and _refs(world) == {"active": r2, "previous": r1}
    assert len(world.refs.events(env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, name="active")) == 3


@pytest.mark.asyncio
async def test_a_request_that_died_before_starting_its_delivery_is_failed_by_the_next_promotion(world: World) -> None:
    r1, r2 = await _two_revisions(world)
    world.applier.fail = KeyboardInterrupt()  # the process going away between filing and starting
    with pytest.raises(KeyboardInterrupt):
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    [stranded] = world.promotion.list_promotions(BOT).items
    assert stranded.status == PromotionStatus.PENDING
    world.applier.fail = None
    import agentclaw.community.core.bot_genome.promotion.service as service_module

    later = service_module._now() + timedelta(seconds=service_module.STALE_PENDING_SECONDS + 1)
    original = service_module._now
    service_module._now = lambda: later
    try:
        await world.promotion.promote(BOT, revision=r2, ref="active", expected_revision=None, actor=OWNER, reason="y")
    finally:
        service_module._now = original
    assert world.promotion.get_promotion(BOT, stranded.id).status == PromotionStatus.APPLY_FAILED


@pytest.mark.asyncio
async def test_a_stranded_promotion_whose_apply_still_holds_the_bot_is_left_to_that_apply(world: World) -> None:
    """A request that died after starting its apply but before recording the apply id leaves a `pending` row
    whose apply still holds the bot's lock. The next promotion is refused busy and leaves that row alone;
    the apply's own end settles it."""
    r1, r2 = await _two_revisions(world)
    world.applier.fail = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        await world.promotion.promote(BOT, revision=r1, ref="active", expected_revision=None, actor=OWNER, reason="x")
    [stranded] = world.promotion.list_promotions(BOT).items
    world.applier.fail = ApplyBusy("its apply still holds bot b")
    import agentclaw.community.core.bot_genome.promotion.service as service_module

    later = service_module._now() + timedelta(seconds=service_module.STALE_PENDING_SECONDS + 1)
    original = service_module._now
    service_module._now = lambda: later
    try:
        with pytest.raises(ApplyBusy):
            await world.promotion.promote(
                BOT, revision=r2, ref="active", expected_revision=None, actor=OWNER, reason="y"
            )
    finally:
        service_module._now = original
    assert world.promotion.get_promotion(BOT, stranded.id).status == PromotionStatus.PENDING
