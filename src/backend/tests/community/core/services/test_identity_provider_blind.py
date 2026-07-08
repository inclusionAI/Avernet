"""IdentityService identity I/O is provider-blind via dispatch_addressed.

The drift fix: a teclaw (or baas) bot's identity read/write must route through the
device filesystem addressed as ``identity/<file>`` — NOT fall through to a local
OSS write. Before consolidation, IdentityService only knew arca+local, so teclaw
identity edits silently hit a dead local path. These tests pin the routing.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.services.identity import IdentityService


def _service(*, provider: str, engine: str):
    """IdentityService with a bound bot of the given provider/engine."""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": engine}
    bot_repo.get_by_id.return_value = {"active_engine": engine}

    ctx = MagicMock()
    ctx.provider = provider
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = ctx

    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"# from engine")
    device_fs.write_file = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs

    svc = IdentityService(
        path_factory=MagicMock(),
        publish_repo=MagicMock(),
        bot_repo=bot_repo,
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
    )
    return svc, dispatcher, device_fs


@pytest.mark.asyncio
async def test_teclaw_write_routes_to_device_identity_namespace():
    svc, dispatcher, device_fs = _service(provider="teclaw", engine="teclaw")

    await svc.write_identity_file("staff", "100018", "bot-1", "IDENTITY.md", "# c", "100018")

    # routed through the factory with coordinates, addressed identity/<file>
    dispatcher.dispatch_addressed.assert_called_once()
    _, kwargs = dispatcher.dispatch_addressed.call_args
    assert kwargs["namespace"] == "identity"
    assert kwargs["engine_type"] == "teclaw"
    assert kwargs["bot_id"] == "bot-1"
    device_fs.write_file.assert_awaited_once_with("identity/IDENTITY.md", b"# c")


@pytest.mark.asyncio
async def test_teclaw_read_routes_to_device_identity_namespace():
    svc, dispatcher, device_fs = _service(provider="teclaw", engine="teclaw")

    content = await svc.read_identity_file("staff", "100018", "bot-1", "IDENTITY.md", "100018")

    assert content == "# from engine"
    device_fs.read_file.assert_awaited_once_with("identity/IDENTITY.md")


@pytest.mark.asyncio
async def test_baas_write_routes_through_factory():
    svc, dispatcher, device_fs = _service(provider="baas", engine="openclaw")

    await svc.write_identity_file("staff", "100018", "bot-1", "RULES.md", "# r", "100018")

    device_fs.write_file.assert_awaited_once_with("identity/RULES.md", b"# r")


@pytest.mark.asyncio
async def test_high_level_update_bot_file_routes_to_device():
    """update_bot_file (the HTTP-facing method) also goes provider-blind."""
    svc, dispatcher, device_fs = _service(provider="teclaw", engine="teclaw")

    resp = await svc.update_bot_file("staff", "100018", "bot-1", "IDENTITY.md", "# c", "100018")

    assert resp.success is True
    device_fs.write_file.assert_awaited_once_with("identity/IDENTITY.md", b"# c")


def _publish_service(*, provider: str, engine: str, ext: dict):
    """IdentityService whose publish_repo returns a record with the given ext."""
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": engine}
    bot_repo.get_by_id.return_value = {"active_engine": engine}

    record = MagicMock()
    record.ext = ext
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = record

    ctx = MagicMock()
    ctx.provider = provider
    resolver = MagicMock()
    resolver.resolve_for_binding.return_value = ctx

    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"# stage content")
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs

    svc = IdentityService(
        path_factory=MagicMock(), publish_repo=publish_repo,
        bot_repo=bot_repo, resolver=resolver, device_fs_dispatcher=dispatcher,
    )
    return svc, resolver, device_fs


@pytest.mark.asyncio
async def test_publish_read_uses_stage_binding_via_resolve_for_binding():
    svc, resolver, device_fs = _publish_service(
        provider="teclaw", engine="teclaw", ext={"binding": {"online": 7}},
    )

    resp = await svc.get_bot_file(
        "staff", "100018", "bot-1", "IDENTITY.md", "op-1", publish_id="55",
    )

    assert resp.content == "# stage content"
    # resolved by the stage bind_id (7), not the bot's draft binding;
    # device-affinity = operator_id (pre-refactor service behavior)
    resolver.resolve_for_binding.assert_called_once_with(7, "op-1", bot_id="bot-1")
    device_fs.read_file.assert_awaited_once_with("identity/IDENTITY.md")


@pytest.mark.asyncio
async def test_publish_read_prefers_online_then_verify():
    svc, resolver, _ = _publish_service(
        provider="baas", engine="openclaw", ext={"binding": {"verify": 3}},
    )

    await svc.get_bot_file("staff", "100018", "bot-1", "RULES.md", "op-1", publish_id="55")

    resolver.resolve_for_binding.assert_called_once_with(3, "op-1", bot_id="bot-1")


@pytest.mark.asyncio
async def test_publish_read_missing_binding_returns_empty_no_fallthrough():
    svc, resolver, device_fs = _publish_service(
        provider="teclaw", engine="teclaw", ext={},  # no binding
    )

    resp = await svc.get_bot_file(
        "staff", "100018", "bot-1", "IDENTITY.md", "op-1", publish_id="55",
    )

    assert resp.content == ""
    resolver.resolve_for_binding.assert_not_called()
    # does NOT fall through to the draft read
    resolver.resolve_for_bot.assert_not_called()
