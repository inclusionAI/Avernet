"""`ensure_active_baseline`: RSI 冷启动的基因组侧答案——只记录，不 apply，CAS 竞态安全。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.bot_genome.baseline import ensure_active_baseline
from agentclaw.community.core.bot_genome.errors import RefConflict
from agentclaw.community.core.bot_genome.models import BotRef, Provenance, ProvenanceKind, RevisionStatus
from tests.community.core.bot_genome.example_blobs import SUPPORT_BOT, spec_of
from tests.community.core.bot_genome.service_fixtures import World
from tests.community.core.bot_genome.test_capture import FakeReader

BOT = BotRef(entity_id="staff_entity_1", bot_id="bot_1", owner_id="user_1", engine_type="openclaw", bot_type="personal")
ACTOR = Provenance(ProvenanceKind.USER, "user_1")


def _active(world: World):
    return next(ref for ref in world.registry.list_refs(BOT) if ref.name == "active")


@pytest.mark.asyncio
async def test_the_first_call_records_the_bot_as_the_baseline(tmp_path) -> None:
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))

    baseline = await ensure_active_baseline(world.registry, BOT, actor=ACTOR)

    assert baseline.created is True
    revision = world.registry.get_revision(BOT, baseline.revision)
    assert revision.revision.status is RevisionStatus.PROMOTED
    assert revision.revision.parent is None
    assert _active(world).revision == baseline.revision
    assert _active(world).moved_by.actor == "user_1"


@pytest.mark.asyncio
async def test_an_existing_baseline_is_returned_untouched(tmp_path, monkeypatch) -> None:
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    first = await ensure_active_baseline(world.registry, BOT, actor=ACTOR)
    capture = AsyncMock(wraps=world.registry.capture)
    monkeypatch.setattr(world.registry, "capture", capture)

    second = await ensure_active_baseline(world.registry, BOT, actor=ACTOR)

    assert second.revision == first.revision
    assert second.created is False
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lost_cas_race_observes_the_winners_baseline(tmp_path, monkeypatch) -> None:
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    real_move = world.registry.move_ref

    def raced(bot, update, *, actor, mover):
        # 并发胜出者先完成了同一内容的一步，这次调用才撞线。
        real_move(bot, update, actor=actor, mover=mover)
        raise RefConflict("active moved first", expected=update.expected_revision, current=update.revision)

    monkeypatch.setattr(world.registry, "move_ref", raced)

    baseline = await ensure_active_baseline(world.registry, BOT, actor=ACTOR)

    assert baseline.created is False
    assert _active(world).revision == baseline.revision
