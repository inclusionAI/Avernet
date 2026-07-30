"""Unit tests for EngineRuntimeRelay (Track C, Task 3).

The relay is the only place Track C crosses into a device, so these cover the
four properties that must hold on every public runtime request: owner-scoped
bot resolution before any device work, single-point device resolution, engine
envelope normalisation, and transport-failure translation.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceContext,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.relay import EngineRuntimeRelay
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)


OWNER = "owner-1"
BOT = "bot-1"


class _BotService:
    """Stands in for BotService.get_bot's owner-scoped lookup."""

    def __init__(self, bots: dict[tuple[str, str], dict] | None = None) -> None:
        self._bots = bots if bots is not None else {(BOT, OWNER): {"bot_id": BOT}}
        self.calls: list[tuple[str, str]] = []

    def get_bot(self, bot_id: str, user_id: str) -> dict:
        self.calls.append((bot_id, user_id))
        bot = self._bots.get((bot_id, user_id))
        if bot is None:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return bot


class _Resolver:
    def __init__(self, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def resolve_for_bot(self, bot_id, user_id, *, device_uuid=None):
        self.calls.append((bot_id, user_id))
        if self._raises is not None:
            raise self._raises
        return DeviceContext(
            provider="local",
            conn_info={"url": "http://device"},
            binding_id=1,
            bot_id=bot_id,
            user_id=user_id,
            bot_type="personal",
        )


_UNSET = object()


class _Transport:
    def __init__(self, result: object = _UNSET, raises: Exception | None = None) -> None:
        # Sentinel, not ``None``: ``None`` is itself one of the malformed bodies
        # under test, so it cannot double as "use the default".
        self._result = {"success": True, "data": {}} if result is _UNSET else result
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    async def invoke(self, conn_info, method, path, body=None, params=None, *, timeout=None):
        self.calls.append((method, path))
        if self._raises is not None:
            raise self._raises
        return self._result

    async def stream(self, *a, **k):  # pragma: no cover - unused here
        raise NotImplementedError


def _relay(bot_service=None, resolver=None, transport=None) -> EngineRuntimeRelay:
    return EngineRuntimeRelay(
        bot_service or _BotService(),
        resolver or _Resolver(),
        transport or _Transport(),
    )


# ── isolation: bot resolution precedes any device work ────────────────────────


@pytest.mark.asyncio
async def test_foreign_bot_raises_before_touching_the_device():
    """The transport must never be invoked for a bot the caller does not own.

    A 404 alone would not prove this: the Track A guard constrains SQL, not a
    device call, so a relay that forwarded first and filtered after would still
    have reached someone else's device.
    """
    resolver, transport = _Resolver(), _Transport()
    relay = _relay(_BotService({}), resolver, transport)

    with pytest.raises(BotNotFoundError):
        await relay.call(bot_id=BOT, owner_id="someone-else", method="GET", path="/api/sessions")

    assert transport.calls == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_bot_is_resolved_with_the_callers_owner_id():
    bot_service = _BotService()
    await _relay(bot_service).call(
        bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
    )
    assert bot_service.calls == [(BOT, OWNER)]


# ── device readiness ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc", [DeviceNotBoundError("no binding"), ConnInfoBuildError("build failed")]
)
async def test_unreachable_device_is_one_retryable_error(exc):
    relay = _relay(resolver=_Resolver(raises=exc))
    with pytest.raises(EngineDeviceNotReadyError):
        await relay.call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions")


@pytest.mark.asyncio
async def test_unknown_provider_is_not_reported_as_not_ready():
    """Bad binding data is ours to fix; retrying will never help the caller."""
    relay = _relay(resolver=_Resolver(raises=UnknownProviderError("bogus")))
    with pytest.raises(UnknownProviderError):
        await relay.call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions")


# ── envelope normalisation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_envelope_is_normalised():
    transport = _Transport(
        {"success": True, "data": [{"id": "s1"}], "total": 7, "warning": "partial"}
    )
    result = await _relay(transport=transport).call(
        bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
    )
    assert result.data == [{"id": "s1"}]
    assert result.total == 7
    assert result.warning == "partial"


@pytest.mark.asyncio
async def test_absent_total_is_none_not_zero():
    """Most engine list routes omit total; unknown is not empty."""
    result = await _relay(transport=_Transport({"success": True, "data": []})).call(
        bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
    )
    assert result.total is None


@pytest.mark.asyncio
async def test_absent_warning_is_empty_string():
    result = await _relay(transport=_Transport({"success": True, "data": 1})).call(
        bot_id=BOT, owner_id=OWNER, method="GET", path="/api/models"
    )
    assert result.warning == ""


@pytest.mark.asyncio
async def test_success_false_inside_http_200_raises():
    """The engine reports business failure inside a 200; it must not pass."""
    transport = _Transport({"success": False, "message": "internal detail"})
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
async def test_missing_success_key_is_treated_as_failure():
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=_Transport({"data": {}})).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, [], "text", 42])
async def test_non_envelope_body_raises(body):
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=_Transport(result=body)).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
async def test_non_integer_total_is_dropped_rather_than_coerced():
    result = await _relay(
        transport=_Transport({"success": True, "data": [], "total": "many"})
    ).call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions")
    assert result.total is None


# ── transport failure translation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_501_becomes_capability_unsupported():
    transport = _Transport(raises=DeviceAdapterHTTPStatusError(501, "no such capability"))
    with pytest.raises(EngineCapabilityUnsupportedError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/nodes"
        )


@pytest.mark.asyncio
async def test_endpoint_not_found_becomes_capability_unsupported():
    transport = _Transport(raises=DeviceAdapterEndpointNotFoundError("no route"))
    with pytest.raises(EngineCapabilityUnsupportedError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 500, 502, 503])
async def test_other_statuses_become_upstream_errors(status):
    transport = _Transport(raises=DeviceAdapterHTTPStatusError(status, "boom"))
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
async def test_timeout_propagates_unwrapped():
    """504 is already the right public answer; wrapping would lose it."""
    transport = _Transport(raises=DeviceAdapterTimeoutError("too slow"))
    with pytest.raises(DeviceAdapterTimeoutError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
async def test_method_and_path_are_forwarded_verbatim():
    transport = _Transport()
    await _relay(transport=transport).call(
        bot_id=BOT, owner_id=OWNER, method="DELETE", path="/api/sessions/abc/messages"
    )
    assert transport.calls == [("DELETE", "/api/sessions/abc/messages")]
