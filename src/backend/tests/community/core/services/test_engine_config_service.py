"""EngineConfigService reads a publish record's engine config provider-blind.

Mirrors the IdentityService publish-read pattern: resolve the stage bind_id via
resolve_for_binding, dispatch_addressed(namespace=config), read the canonical
"config/teclaw.json" logical path. The provider→file mapping lives in the dispatcher
(covered by test_config_addressing); here the device_fs is faked, so read_file is
always called with the canonical logical path regardless of provider.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError
from agentclaw.community.core.services.engine_config import EngineConfigService


def _record(*, status: str = "success", binding: dict | None = None,
            owner_id: str = "100018", source_bot_id: str = "default"):
    rec = MagicMock()
    rec.id = 7
    rec.owner_id = owner_id
    rec.source_bot_id = source_bot_id
    rec.status = status
    rec.ext = {"binding": binding} if binding is not None else {}
    return rec


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """The error arca/baas ``read_file`` raises — it re-raises every failing status.

    Built from real httpx objects rather than a mock so the ``exc.response.status_code``
    the service reads is the genuine attribute path, not one a MagicMock would answer
    for regardless of what the service asked.
    """
    request = httpx.Request("POST", "https://device.invalid/api/file/read")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


def _service(*, read_return=b'{"a": 1}', resolve_raises=None, provider="arca"):
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"entity_id": "100018", "entity_type": "staff"}

    resolver = MagicMock()
    if resolve_raises is not None:
        resolver.resolve_for_binding.side_effect = resolve_raises
    else:
        ctx = MagicMock()
        ctx.provider = provider
        resolver.resolve_for_binding.return_value = ctx

    device_fs = MagicMock()
    if isinstance(read_return, Exception):
        device_fs.read_file = AsyncMock(side_effect=read_return)
    else:
        device_fs.read_file = AsyncMock(return_value=read_return)
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs

    svc = EngineConfigService(
        bot_repo=bot_repo, resolver=resolver, device_fs_dispatcher=dispatcher,
    )
    return svc, resolver, dispatcher, device_fs


@pytest.mark.asyncio
async def test_reads_config_via_stage_binding_and_canonical_path():
    svc, resolver, dispatcher, device_fs = _service(read_return=b'{"model": "x"}')

    data = await svc.read_publish_config(
        _record(status="success", binding={"online": 7}), "openclaw"
    )

    assert data == {"model": "x"}
    # resolved by the stage bind_id (online=7), not the bot's draft binding
    resolver.resolve_for_binding.assert_called_once_with(7, "100018", bot_id="default")
    _, kwargs = dispatcher.dispatch_addressed.call_args
    assert kwargs["namespace"] == "config"
    assert kwargs["engine_type"] == "openclaw"
    assert kwargs["bot_id"] == "default"
    # canonical logical path for every provider
    device_fs.read_file.assert_awaited_once_with("config/teclaw.json")


@pytest.mark.asyncio
async def test_validating_status_uses_verify_binding():
    svc, resolver, _, _ = _service()

    await svc.read_publish_config(
        _record(status="validating", binding={"online": 9, "verify": 3}), "openclaw"
    )

    resolver.resolve_for_binding.assert_called_once_with(3, "100018", bot_id="default")


@pytest.mark.asyncio
async def test_missing_stage_binding_raises():
    """No resolvable active-stage bind_id is a real failure — surface it, don't
    return an empty config."""
    svc, resolver, dispatcher, _ = _service()

    with pytest.raises(DeviceNotBoundError):
        await svc.read_publish_config(_record(status="success", binding={}), "openclaw")

    resolver.resolve_for_binding.assert_not_called()
    dispatcher.dispatch_addressed.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_failure_propagates_not_swallowed():
    """A binding that can't be resolved is a real failure — it must surface, not be
    masked as an empty config."""
    svc, _, dispatcher, _ = _service(resolve_raises=DeviceNotBoundError("nope"))

    with pytest.raises(DeviceNotBoundError):
        await svc.read_publish_config(
            _record(status="success", binding={"online": 7}), "openclaw"
        )

    dispatcher.dispatch_addressed.assert_not_called()


@pytest.mark.asyncio
async def test_empty_or_missing_file_returns_empty_dict():
    for ret in (None, b"", b"   "):
        svc, _, _, _ = _service(read_return=ret)
        data = await svc.read_publish_config(
            _record(status="success", binding={"online": 7}), "openclaw"
        )
        assert data == {}


@pytest.mark.asyncio
async def test_malformed_json_propagates():
    svc, _, _, _ = _service(read_return=b"not json{")

    with pytest.raises(json.JSONDecodeError):
        await svc.read_publish_config(
            _record(status="success", binding={"online": 7}), "openclaw"
        )


# ── bot-level read + write (resolve_for_bot + CONFIG_NS) ─────────────────────


def _bot_service(*, read_return=b'{"a": 1}', resolve_raises=None, provider="arca"):
    """A service whose resolver/dispatcher are faked for the bot-level (for_bot) path."""
    resolver = MagicMock()
    if resolve_raises is not None:
        resolver.resolve_for_bot.side_effect = resolve_raises
    else:
        ctx = MagicMock()
        ctx.provider = provider
        resolver.resolve_for_bot.return_value = ctx

    device_fs = MagicMock()
    if isinstance(read_return, Exception):
        device_fs.read_file = AsyncMock(side_effect=read_return)
    else:
        device_fs.read_file = AsyncMock(return_value=read_return)
    device_fs.write_file = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs

    svc = EngineConfigService(
        bot_repo=MagicMock(), resolver=resolver, device_fs_dispatcher=dispatcher,
    )
    return svc, resolver, dispatcher, device_fs


_BOT_COORDS = dict(
    bot_id="default", owner_id="100018", entity_id="100018",
    entity_type="staff", engine_type="openclaw",
)


@pytest.mark.asyncio
async def test_read_bot_config_parses_via_resolve_for_bot():
    svc, resolver, dispatcher, device_fs = _bot_service(read_return=b'{"k": "v"}')

    data = await svc.read_bot_config(**_BOT_COORDS)

    assert data == {"k": "v"}
    resolver.resolve_for_bot.assert_called_once_with("default", "100018")
    _, kwargs = dispatcher.dispatch_addressed.call_args
    assert kwargs["namespace"] == "config"
    assert kwargs["engine_type"] == "openclaw"
    device_fs.read_file.assert_awaited_once_with("config/teclaw.json")


@pytest.mark.asyncio
async def test_read_bot_config_empty_file_returns_empty_dict():
    for ret in (None, b"", b"  \n"):
        svc, _, _, _ = _bot_service(read_return=ret)
        assert await svc.read_bot_config(**_BOT_COORDS) == {}


@pytest.mark.asyncio
async def test_read_bot_config_malformed_propagates():
    svc, _, _, _ = _bot_service(read_return=b"{bad")
    with pytest.raises(json.JSONDecodeError):
        await svc.read_bot_config(**_BOT_COORDS)


@pytest.mark.asyncio
async def test_write_bot_config_serializes_and_targets_canonical_path():
    svc, resolver, dispatcher, device_fs = _bot_service()

    await svc.write_bot_config(**_BOT_COORDS, config={"x": 1, "y": "z"})

    resolver.resolve_for_bot.assert_called_once_with("default", "100018")
    _, kwargs = dispatcher.dispatch_addressed.call_args
    assert kwargs["namespace"] == "config"
    path, payload = device_fs.write_file.await_args.args
    assert path == "config/teclaw.json"
    # byte-identical to legacy update_engine_config serialization
    assert payload == json.dumps({"x": 1, "y": "z"}, ensure_ascii=False, indent=2).encode("utf-8")


@pytest.mark.asyncio
async def test_bot_config_resolve_failure_propagates():
    svc, _, dispatcher, _ = _bot_service(resolve_raises=DeviceNotBoundError("unbound"))
    with pytest.raises(DeviceNotBoundError):
        await svc.read_bot_config(**_BOT_COORDS)
    dispatcher.dispatch_addressed.assert_not_called()


# ── an absent config file, however the provider says so ──────────────────────
#
# Regression: a bot whose engine config had never been written answered 500 on
# GET /openapi/v1/bots/{bot_id}/engine-config. arca/baas report an absent file as
# HTTP 404 and their read_file re-raises it (deliberately — a swallowed 401 once
# read as "file gone"), nothing between there and the router caught it, and
# httpx.HTTPStatusError is not in ENVELOPE_ERRORS, so it escaped as an unhandled
# 500. The domain contract is "absent means empty" for every provider.


@pytest.mark.asyncio
async def test_read_bot_config_absent_file_404_returns_empty_dict():
    svc, _, _, device_fs = _bot_service(read_return=_http_status_error(404))

    assert await svc.read_bot_config(**_BOT_COORDS) == {}
    device_fs.read_file.assert_awaited_once_with("config/teclaw.json")


@pytest.mark.asyncio
async def test_read_publish_config_absent_file_404_returns_empty_dict():
    svc, _, _, _ = _service(read_return=_http_status_error(404))

    data = await svc.read_publish_config(
        _record(status="success", binding={"online": 7}), "openclaw"
    )

    assert data == {}


@pytest.mark.asyncio
async def test_read_bot_config_non_404_device_status_propagates():
    """A proxy 401 / sandbox 5xx is a real failure and must not read as "no config".

    Reporting one as an empty config would invite the caller to overwrite a config
    they never managed to read — the same swallowed-401 failure mode that made
    read_file re-raise every status in the first place.
    """
    for status in (401, 403, 500, 502):
        svc, _, _, _ = _bot_service(read_return=_http_status_error(status))
        with pytest.raises(httpx.HTTPStatusError):
            await svc.read_bot_config(**_BOT_COORDS)


@pytest.mark.asyncio
async def test_read_publish_config_non_404_device_status_propagates():
    for status in (401, 500):
        svc, _, _, _ = _service(read_return=_http_status_error(status))
        with pytest.raises(httpx.HTTPStatusError):
            await svc.read_publish_config(
                _record(status="success", binding={"online": 7}), "openclaw"
            )
