"""Unit tests for EngineConnectionService (Track C, Task 10)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.engine_runtime.connection import (
    CONNECTION_TTL_SECONDS,
    EngineConnectionService,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
    EngineDeviceNotReadyError,
    EngineUpstreamError,
)

OWNER = "owner-1"
BOT = "bot-1"


class _Bots:
    def __init__(self, engine="openclaw", bot_type="personal"):
        self.engine = engine
        self.bot_type = bot_type
        self.calls = []

    def get_bot(self, bot_id, user_id):
        self.calls.append((bot_id, user_id))
        if (bot_id, user_id) != (BOT, OWNER):
            raise BotNotFoundError(bot_id)
        return {
            "bot_id": bot_id,
            "bot_type": self.bot_type,
            "active_engine": self.engine,
        }


class _Bindings:
    """Stands in for the binding repository's owner-scoped active lookup.

    Counts calls: this endpoint must reach the device provider exactly once,
    and the regression it guards is a second, redundant ``get_ws_info``.
    """

    def __init__(self, binding_id: int | None = 42, raises=None):
        self._binding_id = binding_id
        self.raises = raises
        self.calls: list[tuple[str, str]] = []

    def get_active_by_bot_and_owner(self, bot_id, owner_id):
        self.calls.append((bot_id, owner_id))
        if self.raises:
            raise self.raises
        if self._binding_id is None:
            return None
        return SimpleNamespace(id=self._binding_id)


class _Devices:
    def __init__(self, **overrides):
        defaults = dict(
            type="remote", target="tgt", token="tok", url="", available=True
        )
        self.info = SimpleNamespace(**{**defaults, **overrides})
        self.kwargs = None
        self.raises: Exception | None = None

    def get_device_connection(self, **kwargs):
        self.kwargs = kwargs
        if self.raises is not None:
            raise self.raises
        return self.info


class _Sandbox:
    def __init__(self, base="https://gw.example"):
        self._base = base

    def proxy_base_url(self):
        return self._base


def _svc(bots=None, bindings=None, devices=None, sandbox=None):
    return EngineConnectionService(
        bots or _Bots(), bindings or _Bindings(), devices or _Devices(),
        sandbox or _Sandbox(),
    )


def _build(svc):
    return svc.build(bot_id=BOT, owner_id=OWNER)


def test_foreign_bot_raises_before_resolving_a_device():
    bindings = _Bindings(raises=AssertionError("must not be reached"))
    with pytest.raises(BotNotFoundError):
        _svc(bindings=bindings).build(bot_id=BOT, owner_id="someone-else")


def test_the_resolved_owner_is_passed_as_the_operator():
    """``get_device_connection`` has a wider permission model of its own —
    public bots and collaborators. Passing the resolved owner means it can
    never widen this owner-only surface."""
    devices = _Devices()
    _build(_svc(devices=devices))
    assert devices.kwargs["operator"].staff_id == OWNER
    assert devices.kwargs["binding_id"] == 42
    assert devices.kwargs["ttl"] == CONNECTION_TTL_SECONDS


def test_relay_mode_is_requested_so_the_provider_returns_a_finished_url():
    """The BaaS provider fills ``url`` only for relay mode. Leaving the mode
    unset gets a bare routing target back, which a deployment without a proxy
    gateway cannot turn into a URL at all."""
    devices = _Devices()
    _build(_svc(devices=devices))
    assert devices.kwargs["ws_conn_mode"] == "relay"


def test_the_engines_socket_path_is_requested_from_the_provider():
    """The relay URL is built server-side *around* this path, so it has to be
    the bot's own engine. The provider default is openclaw's, and the engine
    closes a socket whose pinned engine is not the active one."""
    devices = _Devices()
    _build(_svc(bots=_Bots(engine="claude_code"), devices=devices))
    assert devices.kwargs["path"] == "/api/claude_code/ws"


def test_a_relayed_url_is_not_appended_to():
    """It already ends in the path we asked for; appending again would give
    ``…/api/openclaw/ws/api/openclaw/ws``, which cannot connect."""
    devices = _Devices(url="wss://relay.example/wsrelay/s1/api/openclaw/ws")
    result = _build(_svc(devices=devices))
    assert result.sockets[0].url == "wss://relay.example/wsrelay/s1/api/openclaw/ws"


def test_the_socket_carries_the_ws_credential_not_the_http_one():
    """The local provider's healthy path returns http-info's token while the
    address comes from ws-info's target — publishing `token` there would pair a
    ws URL with the wrong credential."""
    devices = _Devices(token="http-tok", ws_token="ws-tok")
    result = _build(_svc(devices=devices))
    assert result.sockets[0].headers["x-proxypass-token"] == "ws-tok"


def test_the_socket_expiry_describes_the_ws_credential():
    """Same pairing for the expiry: `expires_at` describes the http token, which
    is not what was published."""
    devices = _Devices(
        token="http-tok",
        ws_token="ws-tok",
        expires_at="",
        ws_expires_at="2026-07-30T20:00:00Z",
    )
    result = _build(_svc(devices=devices))
    assert result.expires_at == "2026-07-30T20:00:00+00:00"


def test_a_provider_issuing_no_ws_credential_still_uses_its_token():
    """Providers that do not distinguish the two leave `ws_token` empty."""
    devices = _Devices(token="only-tok", ws_token="")
    result = _build(_svc(devices=devices))
    assert result.sockets[0].headers["x-proxypass-token"] == "only-tok"


def test_a_failing_provider_is_an_upstream_error_not_a_500():
    """A ws-info call that times out reaches here as ``BaasDeviceServiceError``.
    The bot's device may be healthy, so this is an upstream fault — and
    uncaught it would be a 500 on a condition that has a name."""
    from agentclaw.community.core.devices.services.baas_device_service import (
        BaasDeviceServiceError,
    )

    devices = _Devices()
    devices.raises = BaasDeviceServiceError("ws-info timed out")
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))


@pytest.mark.parametrize("error_name", ["DeviceNotFoundError", "InvalidDeviceStatusError"])
def test_an_unusable_device_is_not_ready_rather_than_a_500(error_name):
    """No binding, a failed one, or one the operator cannot reach — the same
    class of answer as a device that will not resolve."""
    import agentclaw.community.core.devices.errors as device_errors

    devices = _Devices()
    devices.raises = getattr(device_errors, error_name)("nope")
    with pytest.raises(EngineDeviceNotReadyError):
        _build(_svc(devices=devices))


def test_a_deployment_without_a_proxy_gateway_is_an_upstream_error():
    """``proxy_base_url`` raises in builds with no sandbox runtime. Letting it
    out would 500 on a condition that has a name."""
    from agentclaw.community.plugin_api.sandbox_runtime import (
        SandboxRuntimeUnavailableError,
    )

    class _NoRuntime:
        def proxy_base_url(self):
            raise SandboxRuntimeUnavailableError("no ARCA runtime here")

    with pytest.raises(EngineUpstreamError):
        _build(_svc(sandbox=_NoRuntime()))


def test_proxy_url_is_composed_and_scheme_swapped():
    result = _build(_svc())
    assert result.sockets[0].url == (
        "wss://gw.example/proxypass/tgt/api/openclaw/ws"
    )
    assert result.sockets[0].headers == {"x-proxypass-token": "tok"}


def test_a_provider_supplied_ws_url_is_used_verbatim():
    devices = _Devices(url="wss://relay.example/route/xyz")
    result = _build(_svc(devices=devices))
    assert result.sockets[0].url == "wss://relay.example/route/xyz"


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


def test_only_a_chat_socket_is_offered():
    """A terminal socket was implemented and removed: the spec excludes an
    interactive shell on a tenant's device from v1 at any scope."""
    assert [s.kind for s in _build(_svc()).sockets] == ["chat"]


def test_unbound_device_is_retryable_not_an_internal_error():
    with pytest.raises(EngineDeviceNotReadyError):
        _build(_svc(bindings=_Bindings(binding_id=None)))


def test_unavailable_device_is_not_ready():
    with pytest.raises(EngineDeviceNotReadyError):
        _build(_svc(devices=_Devices(available=False)))


def test_missing_routing_target_is_an_upstream_error_not_a_broken_url():
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=_Devices(target="")))


def test_missing_proxy_base_is_an_upstream_error():
    with pytest.raises(EngineUpstreamError):
        _build(_svc(sandbox=_Sandbox(base="")))


def test_expires_at_falls_back_to_the_ttl_when_the_provider_reports_none():
    from datetime import datetime, timedelta, timezone

    result = _build(_svc())
    expires = datetime.fromisoformat(result.expires_at)
    expected = datetime.now(timezone.utc) + timedelta(seconds=CONNECTION_TTL_SECONDS)
    assert abs((expires - expected).total_seconds()) < 60


def test_expires_at_prefers_the_providers_own_value():
    """The BaaS path ignores the requested TTL and decides server-side, so a
    locally computed expiry there describes a token that does not exist."""
    devices = _Devices(expires_at="2031-05-06T07:08:09Z")
    result = _build(_svc(devices=devices))
    assert result.expires_at == "2031-05-06T07:08:09+00:00"


def test_a_naive_provider_timestamp_is_read_as_utc():
    devices = _Devices(expires_at="2031-05-06T07:08:09")
    assert _build(_svc(devices=devices)).expires_at == "2031-05-06T07:08:09+00:00"


def test_an_unparseable_provider_value_is_not_published_verbatim():
    """``expires_at`` is contractually ISO 8601; garbage falls back to the
    computed bound rather than shipping a string clients cannot parse."""
    from datetime import datetime, timedelta, timezone

    devices = _Devices(expires_at="soon-ish")
    result = _build(_svc(devices=devices))
    expires = datetime.fromisoformat(result.expires_at)
    expected = datetime.now(timezone.utc) + timedelta(seconds=CONNECTION_TTL_SECONDS)
    assert abs((expires - expected).total_seconds()) < 60


def test_result_carries_no_target_type_or_bare_token():
    text = repr(_build(_svc()))
    assert "'tgt'" not in text  # the target appears only inside the URL
    assert "type=" not in text


# ── bot-type gate ─────────────────────────────────────────────────────────


def test_a_service_bot_is_refused_before_a_device_is_touched():
    """The published socket is an operator channel, not a chat channel.

    The engine's WebSocket server advertises the ``sessions.*`` and
    ``exec.approvals.*`` methods and grants ``operator.admin``, and one service
    bot's device holds every caller's sessions. Serving this endpoint there
    would hand the owner over a socket precisely what the sessions group
    answers 501 to refuse over HTTP.
    """
    bindings = _Bindings(raises=AssertionError("must not be reached"))
    devices = _Devices()
    svc = _svc(bots=_Bots(bot_type="service"), bindings=bindings, devices=devices)

    with pytest.raises(EngineBotTypeNotSupportedError):
        _build(svc)

    # Refused at composition time — no device call was made on the way out.
    assert devices.kwargs is None


def test_an_unknown_bot_type_is_refused_rather_than_assumed_personal():
    """The gate is an allowlist. A bot type this build has never heard of is
    not silently treated as the permissive case."""
    with pytest.raises(EngineBotTypeNotSupportedError):
        _build(_svc(bots=_Bots(bot_type="")))
    with pytest.raises(EngineBotTypeNotSupportedError):
        _build(_svc(bots=_Bots(bot_type="something-new")))


def test_a_personal_bot_still_gets_its_socket():
    result = _build(_svc(bots=_Bots(bot_type="personal")))
    assert [s.kind for s in result.sockets] == ["chat"]


def test_the_provider_is_asked_exactly_once():
    """One connection request must make one provider call.

    Resolving through ``DeviceContextResolver`` built full connection info —
    a blocking ``get_ws_info`` on the BaaS path — and then threw all of it away
    except the binding id, because the relay-mode lookup below fetches the URL
    and credential this endpoint actually publishes. Two 30-second timeouts for
    one answer.
    """
    bindings, devices = _Bindings(), _Devices()
    _build(_svc(bindings=bindings, devices=devices))

    # The binding is read from the repository, not built by a provider call...
    assert bindings.calls == [(BOT, OWNER)]
    # ...leaving the relay-mode lookup as the only provider round trip.
    assert devices.kwargs is not None
    assert devices.kwargs["binding_id"] == 42
