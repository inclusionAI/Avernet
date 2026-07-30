"""Unit tests for EngineConnectionService (Track C, Task 10)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
)
from agentclaw.community.core.engine_runtime.connection import (
    CONNECTION_TTL_SECONDS,
    EngineConnectionService,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError,
    EngineUpstreamError,
)

OWNER = "owner-1"
BOT = "bot-1"


class _Bots:
    def __init__(self, engine="openclaw"):
        self.engine = engine
        self.calls = []

    def get_bot(self, bot_id, user_id):
        self.calls.append((bot_id, user_id))
        if (bot_id, user_id) != (BOT, OWNER):
            raise BotNotFoundError(bot_id)
        return {"bot_id": bot_id, "active_engine": self.engine}


class _Resolver:
    def __init__(self, raises=None):
        self.raises = raises

    def resolve_for_bot(self, bot_id, user_id, *, device_uuid=None):
        if self.raises:
            raise self.raises
        return DeviceContext(
            provider="arca", conn_info={}, binding_id=42,
            bot_id=bot_id, user_id=user_id, bot_type="personal",
        )


class _Devices:
    def __init__(self, **overrides):
        defaults = dict(
            type="remote", target="tgt", token="tok", url="", available=True
        )
        self.info = SimpleNamespace(**{**defaults, **overrides})
        self.kwargs = None

    def get_device_connection(self, **kwargs):
        self.kwargs = kwargs
        return self.info


class _Sandbox:
    def __init__(self, base="https://gw.example"):
        self._base = base

    def proxy_base_url(self):
        return self._base


def _svc(bots=None, resolver=None, devices=None, sandbox=None):
    return EngineConnectionService(
        bots or _Bots(), resolver or _Resolver(), devices or _Devices(),
        sandbox or _Sandbox(),
    )


def _build(svc, *, terminal=False):
    return svc.build(bot_id=BOT, owner_id=OWNER, include_terminal=terminal)


def test_foreign_bot_raises_before_resolving_a_device():
    resolver = _Resolver(raises=AssertionError("must not be reached"))
    with pytest.raises(BotNotFoundError):
        _svc(resolver=resolver).build(
            bot_id=BOT, owner_id="someone-else", include_terminal=False
        )


def test_the_resolved_owner_is_passed_as_the_operator():
    """``get_device_connection`` has a wider permission model of its own —
    public bots and collaborators. Passing the resolved owner means it can
    never widen this owner-only surface."""
    devices = _Devices()
    _build(_svc(devices=devices))
    assert devices.kwargs["operator"].staff_id == OWNER
    assert devices.kwargs["binding_id"] == 42
    assert devices.kwargs["ttl"] == CONNECTION_TTL_SECONDS


def test_proxy_url_is_composed_and_scheme_swapped():
    result = _build(_svc())
    assert result.sockets[0].url == (
        "wss://gw.example/proxypass/tgt/api/openclaw/ws"
    )
    assert result.sockets[0].headers == {"x-proxypass-token": "tok"}


def test_a_provider_supplied_ws_url_is_used_verbatim():
    devices = _Devices(url="wss://relay.example/route/xyz")
    result = _build(_svc(devices=devices))
    assert result.sockets[0].url == "wss://relay.example/route/xyz/api/openclaw/ws"


def test_local_devices_are_reached_directly():
    devices = _Devices(type="local", target="127.0.0.1:20003")
    assert _build(_svc(devices=devices)).sockets[0].url.startswith(
        "ws://127.0.0.1:20003"
    )


@pytest.mark.parametrize(
    ("engine", "path"),
    [
        ("openclaw", "/api/openclaw/ws"),
        ("claude_code", "/api/claude_code/ws"),
        ("hermes", "/api/hermes/ws"),
    ],
)
def test_chat_path_follows_the_engine(engine, path):
    """An engine without a dedicated route falls back to the adapter's generic
    one, so a newly-added engine is reachable with no change here."""
    assert _build(_svc(bots=_Bots(engine))).sockets[0].url.endswith(path)


def test_terminal_socket_is_opt_in():
    assert [s.kind for s in _build(_svc()).sockets] == ["chat"]
    assert [s.kind for s in _build(_svc(), terminal=True).sockets] == [
        "chat",
        "terminal",
    ]


def test_unbound_device_is_retryable_not_an_internal_error():
    with pytest.raises(EngineDeviceNotReadyError):
        _build(_svc(resolver=_Resolver(raises=DeviceNotBoundError("none"))))


def test_unavailable_device_is_not_ready():
    with pytest.raises(EngineDeviceNotReadyError):
        _build(_svc(devices=_Devices(available=False)))


def test_missing_routing_target_is_an_upstream_error_not_a_broken_url():
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=_Devices(target="")))


def test_missing_proxy_base_is_an_upstream_error():
    with pytest.raises(EngineUpstreamError):
        _build(_svc(sandbox=_Sandbox(base="")))


def test_expires_at_is_derived_from_the_requested_ttl():
    from datetime import datetime, timedelta, timezone

    result = _build(_svc())
    expires = datetime.fromisoformat(result.expires_at)
    expected = datetime.now(timezone.utc) + timedelta(seconds=CONNECTION_TTL_SECONDS)
    assert abs((expires - expected).total_seconds()) < 60


def test_result_carries_no_target_type_or_bare_token():
    text = repr(_build(_svc(), terminal=True))
    assert "'tgt'" not in text  # the target appears only inside the URL
    assert "type=" not in text
