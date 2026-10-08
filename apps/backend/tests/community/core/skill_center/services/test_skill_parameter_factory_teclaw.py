"""teclaw does not use skill_parameters.json: the factory disables engine IO so
load/save never read/write that file to/from the engine. Non-teclaw is unchanged
(reads/writes the engine-absolute default path)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.skill_center.factories import SkillParameterServiceFactory

pytestmark = pytest.mark.unit


def _factory_and_fs(provider: str):
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = SimpleNamespace(provider=provider)
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=None)
    device_fs.write_file = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = device_fs
    factory = SkillParameterServiceFactory(resolver=resolver, device_fs_dispatcher=dispatcher)
    return factory, device_fs


@pytest.mark.asyncio
async def test_teclaw_skips_engine_read_and_write():
    factory, device_fs = _factory_and_fs("teclaw")
    # create() calls async_load — must NOT read the engine for teclaw
    svc = await factory.create(bot_id="b1", user_id="u1")
    device_fs.read_file.assert_not_awaited()

    # save must NOT write the engine for teclaw
    ok = await svc.save_skill_parameters("my-skill", {"k": "v"})
    device_fs.write_file.assert_not_awaited()
    assert ok is False  # nothing persisted


@pytest.mark.asyncio
async def test_non_teclaw_reads_and_writes_engine():
    factory, device_fs = _factory_and_fs("arca")
    svc = await factory.create(bot_id="b1", user_id="u1")
    device_fs.read_file.assert_awaited()  # load read the engine

    await svc.save_skill_parameters("my-skill", {"k": "v"})
    device_fs.write_file.assert_awaited()  # save wrote the engine
