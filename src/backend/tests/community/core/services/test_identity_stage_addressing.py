"""Stage addressing on ``IdentityService`` — which runtime a read reaches.

The draft half is a byte-for-byte pin: a call that names no stage must resolve
exactly what it always resolved. The published half pins that the runtime comes
from the publish record rather than the bot's own binding, and that the write
refuses a published stage without touching a device.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.engine_runtime.errors import (
    EngineStageNotLiveError,
    EngineStageReadOnlyError,
)
from agentclaw.community.core.engine_runtime.stage import (
    STAGE_DRAFT,
    STAGE_ONLINE,
    STAGE_VERIFY,
)
from agentclaw.community.core.services.identity import IdentityService

BOT = "bot-1"
OWNER = "u-1"
ONLINE_BINDING = 41


def _service(*, bot_type="service", read_return=b"hello"):
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = MagicMock(provider="baas")
    resolver.resolve_for_binding.return_value = MagicMock(provider="baas")

    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=read_return)
    device_fs.write_file = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs

    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {
        "id": 100,
        "bot_id": BOT,
        "bot_type": bot_type,
        "owner_id": OWNER,
        "active_engine": "openclaw",
    }
    publish_repo = MagicMock()
    publish_repo.list_by_source_bot.return_value = [
        SimpleNamespace(
            id=7, status="success", ext={"binding": {"online": ONLINE_BINDING}}
        )
    ]
    svc = IdentityService(
        path_factory=MagicMock(),
        publish_repo=publish_repo,
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
        binding_repo=MagicMock(),
    )
    return svc, resolver, dispatcher, device_fs


@pytest.mark.asyncio
async def test_a_read_naming_no_stage_resolves_the_bots_own_binding():
    svc, resolver, _, _ = _service()

    await svc.get_bot_file("staff", OWNER, BOT, "RULES.md", OWNER)

    resolver.resolve_for_bot.assert_called_once_with(BOT, OWNER)
    resolver.resolve_for_binding.assert_not_called()


@pytest.mark.asyncio
async def test_reading_online_resolves_the_published_binding():
    svc, resolver, _, _ = _service()

    resp = await svc.get_bot_file(
        "staff", OWNER, BOT, "RULES.md", OWNER, stage=STAGE_ONLINE
    )

    assert resp.content == "hello"
    resolver.resolve_for_binding.assert_called_once_with(
        ONLINE_BINDING, OWNER, bot_id=BOT
    )
    resolver.resolve_for_bot.assert_not_called()


@pytest.mark.asyncio
async def test_a_published_stage_on_a_personal_bot_is_not_live():
    svc, resolver, _, _ = _service(bot_type="personal")

    with pytest.raises(EngineStageNotLiveError):
        await svc.get_bot_file(
            "staff", OWNER, BOT, "RULES.md", OWNER, stage=STAGE_ONLINE
        )

    resolver.resolve_for_binding.assert_not_called()


@pytest.mark.asyncio
async def test_listing_resolves_the_runtime_once_for_all_sixteen_files():
    """Not once per file.

    Resolution is synchronous, and synchronous work in a coroutine runs before
    its first await — so a per-file resolve would execute sixteen publish scans
    and sixteen provider calls back to back on the event loop, not concurrently.
    """
    svc, resolver, dispatcher, device_fs = _service()

    presence = await svc.list_bot_files(
        "staff", OWNER, BOT, OWNER, stage=STAGE_ONLINE
    )

    assert len(presence) == 16
    assert resolver.resolve_for_binding.call_count == 1
    assert dispatcher.dispatch_addressed.call_count == 1
    assert device_fs.read_file.await_count == 16


@pytest.mark.asyncio
async def test_listing_reports_every_file_from_the_one_addressed_runtime():
    svc, _, _, _ = _service(read_return=b"")

    presence = await svc.list_bot_files(
        "staff", OWNER, BOT, OWNER, stage=STAGE_ONLINE
    )

    assert {exists for _ft, exists in presence} == {False}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [STAGE_VERIFY, STAGE_ONLINE])
async def test_writing_a_published_stage_is_refused_and_writes_nothing(stage):
    svc, resolver, dispatcher, device_fs = _service()

    with pytest.raises(EngineStageReadOnlyError):
        await svc.update_bot_file(
            "staff", OWNER, BOT, "RULES.md", "new", OWNER, stage=stage
        )

    dispatcher.dispatch_addressed.assert_not_called()
    device_fs.write_file.assert_not_awaited()
    resolver.resolve_for_bot.assert_not_called()
    resolver.resolve_for_binding.assert_not_called()


@pytest.mark.asyncio
async def test_a_malformed_write_still_hears_which_part_is_wrong():
    """The stage guard runs before anything is *resolved*, but after the two
    static validators — so a request that is both malformed and stage-addressed
    is told about the malformed part, which it can fix."""
    from agentclaw.community.core.services.identity import (
        InvalidIdentityEntityTypeError,
    )

    svc, _, _, _ = _service()

    with pytest.raises(InvalidIdentityEntityTypeError):
        await svc.update_bot_file(
            "bogus", OWNER, BOT, "RULES.md", "new", OWNER, stage=STAGE_ONLINE
        )


@pytest.mark.asyncio
async def test_the_draft_write_still_lands():
    svc, resolver, _, device_fs = _service()

    await svc.update_bot_file(
        "staff", OWNER, BOT, "RULES.md", "new", OWNER, stage=STAGE_DRAFT
    )

    # Every resolution went through the bot's own binding and none through a
    # published one. Not an exact call count: RULES.md is a reference file and
    # the engine is openclaw, so the write is followed by the AGENTS.md sync,
    # which reads and writes more files through the same draft runtime.
    assert resolver.resolve_for_bot.call_args_list == [
        (((BOT, OWNER)), {})
    ] * resolver.resolve_for_bot.call_count
    resolver.resolve_for_binding.assert_not_called()
    device_fs.write_file.assert_awaited()


@pytest.mark.asyncio
async def test_a_publish_id_wins_over_a_stage_and_a_bogus_stage_is_still_refused():
    """The documented precedence, and its limit.

    ``publish_id`` names one exact release and is the more specific address, so
    it wins. A stage name that is not a stage is still refused, rather than
    silently discarded because the other branch was taken.
    """
    svc, _, _, _ = _service()

    with pytest.raises(EngineStageNotLiveError):
        await svc.get_bot_file(
            "staff", OWNER, BOT, "RULES.md", OWNER, publish_id="7", stage="onlien"
        )
