"""Unit tests for EngineConnectionService (Track C, Task 10)."""

from __future__ import annotations

import dataclasses
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
from agentclaw.community.di.config import GatewayEndpoint

OWNER = "owner-1"
BOT = "bot-1"


@pytest.fixture(autouse=True)
def _pinned_env(monkeypatch):
    """This service no longer reads the environment — the composition root hands
    it an already-resolved endpoint — but the device and bot stubs below reach
    code that still does. Pinned so a runner with ``SERVER_ENV`` set cannot make
    these outcomes depend on where they ran."""
    monkeypatch.delenv("SERVER_ENV", raising=False)
    monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
    monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)


class _Bots:
    def __init__(self, engine="openclaw", bot_type="personal", public="0"):
        self.engine = engine
        self.bot_type = bot_type
        self.public = public
        self.calls = []

    def get_bot(self, bot_id, user_id):
        self.calls.append((bot_id, user_id))
        if (bot_id, user_id) != (BOT, OWNER):
            raise BotNotFoundError(bot_id)
        return {
            "bot_id": bot_id,
            "owner_id": user_id,
            "bot_type": self.bot_type,
            "active_engine": self.engine,
            "public": self.public,
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
    """Stands in for the device provider.

    Models the BaaS relay contract: ``url`` is a finished WebSocket URL the
    provider builds *around the path it is asked for*, addressed to the hop
    behind the gateway. Tests that care about the URL override it; the rest get
    one built from the requested path, so the path this endpoint sends and the
    path a caller ends up connecting to stay tied together the way they are in
    production.
    """

    def __init__(self, **overrides):
        defaults = dict(
            type="remote", target="tgt", token="tok", available=True
        )
        self._url_override = overrides.pop("url", None)
        self.info = SimpleNamespace(url="", **{**defaults, **overrides})
        self.kwargs = None
        self.raises: Exception | None = None

    def get_device_connection(self, **kwargs):
        self.kwargs = kwargs
        if self.raises is not None:
            raise self.raises
        if self._url_override is None:
            target = self.info.target
            self.info.url = f"wss://proxy.example/proxypass/{target}{kwargs['path']}"
        else:
            self.info.url = self._url_override
        return self.info


def _gateway(base="https://gw.example"):
    """The endpoint the composition root resolved for this environment.

    Which of the configured hosts that is, is decided in DI — see
    ``test_config_module`` for the pre/prod selection itself."""
    return GatewayEndpoint(base_url=base)


class _CollaboratorRepo:
    """Stands in for the collaborator table the shared-bot gate reads."""

    def __init__(self, collaborators=None):
        self._collaborators = collaborators or {}
        self.calls = []

    def list_by_bot(self, bot_id, owner_id, env, role=None):
        self.calls.append((bot_id, owner_id))
        return self._collaborators.get((bot_id, owner_id), [])


def _svc(bots=None, bindings=None, devices=None, gateway=None, collaborators=None):
    return EngineConnectionService(
        bots or _Bots(), bindings or _Bindings(), devices or _Devices(),
        gateway if gateway is not None else _gateway(),
        collaborators or _CollaboratorRepo(),
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


def test_a_relay_url_the_gateway_prefix_cannot_express_is_refused():
    """``/wsrelay/{session_id}`` cannot be rebuilt from a target and a path, and
    the gateway only rewrites ``/engine/`` onto ``/proxypass/``. Publishing the
    provider's own URL instead would name the hop behind the gateway."""
    devices = _Devices(url="wss://relay.example/wsrelay/s1/api/openclaw/ws")
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))


def test_the_socket_carries_the_ws_credential_not_the_http_one():
    """The local provider's healthy path returns http-info's token while the
    address comes from ws-info's target — publishing `token` there would pair a
    ws URL with the wrong credential."""
    devices = _Devices(token="http-tok", ws_token="ws-tok")
    result = _build(_svc(devices=devices))
    assert result.sockets[0].url.endswith("?x-proxypass-token=ws-tok")


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
    assert result.sockets[0].url.endswith("?x-proxypass-token=only-tok")


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


def test_a_deployment_fronting_no_gateway_is_an_upstream_error():
    """The community build's normal state. Letting an empty host through would
    publish an address nothing serves, on a condition that has a name."""
    with pytest.raises(EngineUpstreamError):
        _build(_svc(gateway=_gateway(base="")))


def test_a_gateway_base_without_a_scheme_is_an_upstream_error():
    """``gw.example/engine/…`` is not openable. Refused rather than published,
    since the value comes from a deployment overlay this build does not own."""
    with pytest.raises(EngineUpstreamError):
        _build(_svc(gateway=_gateway(base="gw.example")))


def test_the_relay_url_is_readdressed_onto_the_gateway():
    result = _build(_svc())
    assert result.sockets[0].url == (
        "wss://gw.example/engine/tgt/api/openclaw/ws?x-proxypass-token=tok"
    )


def test_the_published_url_never_names_the_hop_behind_the_gateway():
    assert "proxypass/" not in _build(_svc()).sockets[0].url


def test_an_http_gateway_base_becomes_ws():
    """The branch singlebox actually runs — its overlay ships an http base."""
    result = _build(_svc(gateway=_gateway(base="http://127.0.0.1:9999")))
    assert result.sockets[0].url.startswith("ws://127.0.0.1:9999/engine/")


def test_a_gateway_base_already_spelled_as_a_socket_origin_is_kept():
    result = _build(_svc(gateway=_gateway(base="wss://gw.example")))
    assert result.sockets[0].url.startswith("wss://gw.example/engine/")


@pytest.mark.parametrize(
    "base", ["https://gw.example/api", "https://gw.example/http://x"]
)
def test_a_gateway_base_carrying_a_path_is_refused(base):
    """``/engine`` has to sit at the root — that is where the gateway's rewrite
    is anchored. A base with a path would publish ``/api/engine/…``, which the
    gateway does not route, so the socket would be unopenable rather than
    named."""
    with pytest.raises(EngineUpstreamError):
        _build(_svc(gateway=_gateway(base=base)))


def test_a_trailing_slash_and_stray_whitespace_are_normalised():
    result = _build(_svc(gateway=_gateway(base="  https://gw.example//  ")))
    assert result.sockets[0].url.startswith("wss://gw.example/engine/")


def test_a_relay_url_carrying_a_fragment_is_refused():
    """Everything after a ``#`` is a path we would silently drop, and a browser
    never sends a fragment — so the URL is already broken upstream. Named rather
    than re-addressed into something shorter that merely looks valid."""
    devices = _Devices(url="wss://proxy.example/proxypass/tgt#/api/openclaw/ws")
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))


def test_a_provider_query_is_preserved_and_the_credential_appended():
    """The provider owns this URL. Assigning our own query would drop whatever
    it set and fail the socket with nothing pointing at why."""
    devices = _Devices(
        url="wss://proxy.example/proxypass/tgt/api/openclaw/ws?session=s1"
    )
    url = _build(_svc(devices=devices)).sockets[0].url
    assert url == (
        "wss://gw.example/engine/tgt/api/openclaw/ws"
        "?session=s1&x-proxypass-token=tok"
    )


def test_the_provider_path_is_carried_through_verbatim():
    """Whatever the provider put after its routing prefix is what a caller
    connects to — this endpoint holds no opinion about that grammar."""
    devices = _Devices(
        url="wss://proxy.example/proxypass/tgt/v2/api/openclaw/ws"
    )
    url = _build(_svc(devices=devices)).sockets[0].url
    assert url.startswith("wss://gw.example/engine/tgt/v2/api/openclaw/ws?")


@pytest.mark.parametrize("base", ["https://gw.example/#", "https://gw.example/?x=1"])
def test_a_gateway_base_carrying_url_delimiters_is_refused(base):
    """A ``#`` here ends the path before the engine prefix is appended, putting
    the credential in a fragment a browser never sends — the same silent harm
    escaping the target closes, reached through the other half of the URL."""
    with pytest.raises(EngineUpstreamError):
        _build(_svc(gateway=_gateway(base=base)))


@pytest.mark.parametrize(
    "devices",
    [
        pytest.param(_Devices(ws_token="tok\ud800"), id="credential"),
        pytest.param(_Devices(url="wss://p.example/proxypass/t\ud800/ws"), id="relay-url"),
        pytest.param(
            _Devices(type="local", target="127.0.0.1\ud800:20003"), id="local-target"
        ),
    ],
)
def test_an_unencodable_value_is_named_rather_than_a_500(devices):
    """A lone surrogate survives ``json.loads``, so a provider's response can
    carry one. It would then fail either ``quote`` or the response serialiser —
    a 500 on a value we can describe."""
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))


def test_no_upstream_error_message_ever_carries_the_credential():
    """One of these messages interpolates configuration, so the rule that none
    of them interpolates the credential is worth pinning rather than assuming."""
    for gateway in (_gateway(base=""), _gateway(base="gw.example")):
        with pytest.raises(EngineUpstreamError) as excinfo:
            _build(_svc(devices=_Devices(ws_token="secret-tok"), gateway=gateway))
        assert "secret-tok" not in str(excinfo.value)


def test_a_malformed_provider_url_is_named_rather_than_a_500():
    """``urlsplit`` raises on this. The guard exists to produce a named error,
    so letting the parse failure out would defeat its whole purpose."""
    devices = _Devices(url="wss://[bad/proxypass/x")
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))


def test_a_provider_relay_url_of_an_unknown_shape_is_refused():
    """Same guard as the ``/wsrelay/`` case: anything this endpoint cannot
    re-address onto ``/engine/`` is an upstream error, not a passthrough."""
    devices = _Devices(url="wss://relay.example/route/xyz")
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))


def test_only_the_origin_and_routing_prefix_change():
    """Everything past the provider's routing prefix is carried through as the
    provider wrote it; only the origin and that prefix are ours to change."""
    devices = _Devices(url="wss://proxy.example/proxypass/tgt/api/openclaw/ws")
    result = _build(_svc(devices=devices))
    assert result.sockets[0].url == (
        "wss://gw.example/engine/tgt/api/openclaw/ws?x-proxypass-token=tok"
    )


def test_local_devices_are_reached_directly_without_a_credential():
    """The gateway routes to the hop behind it, which cannot reach a device on
    the caller's own machine — and there is no proxy to authenticate to."""
    devices = _Devices(type="local", target="127.0.0.1:20003")
    url = _build(_svc(devices=devices)).sockets[0].url
    assert url == "ws://127.0.0.1:20003/api/openclaw/ws"
    assert "x-proxypass-token" not in url


def test_a_local_device_does_not_need_a_gateway_configured():
    devices = _Devices(type="local", target="127.0.0.1:20003")
    result = _build(_svc(devices=devices, gateway=_gateway(base="")))
    assert result.sockets[0].url == "ws://127.0.0.1:20003/api/openclaw/ws"


def test_no_credential_publishes_no_query_string():
    """An empty ``?x-proxypass-token=`` would fail the handshake with nothing to
    diagnose from. Absent is published as absent, as it was when it was a
    header."""
    devices = _Devices(token="", ws_token="")
    url = _build(_svc(devices=devices)).sockets[0].url
    assert url == "wss://gw.example/engine/tgt/api/openclaw/ws"
    assert "?" not in url


def test_the_credential_is_percent_encoded():
    devices = _Devices(ws_token="a b&c=d")
    url = _build(_svc(devices=devices)).sockets[0].url
    assert url.endswith("?x-proxypass-token=a%20b%26c%3Dd")


def test_the_target_segment_is_not_percent_encoded():
    """``@`` and ``:`` are legal in a path segment and the hop behind the
    gateway matches the target raw."""
    devices = _Devices(target="ARCA_ARCA-SANDBOX-abc@0:20003")
    url = _build(_svc(devices=devices)).sockets[0].url
    assert "/engine/ARCA_ARCA-SANDBOX-abc@0:20003/api/openclaw/ws" in url


def test_a_local_ipv6_target_keeps_its_brackets():
    """``[::1]:20003`` is an authority, not a path segment — percent-encoding the
    brackets yields something no URL parser accepts. Singlebox reclassifies a
    ``::1`` binding as local, so this branch really does see one."""
    devices = _Devices(type="local", target="[::1]:20003")
    url = _build(_svc(devices=devices)).sockets[0].url
    assert url == "ws://[::1]:20003/api/openclaw/ws"


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
    url = _build(_svc(bots=_Bots(engine))).sockets[0].url
    assert url.split("?")[0].endswith(path)


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


def test_neither_socket_model_carries_a_headers_field():
    """The credential lives in the URL and only there. A ``headers`` field would
    publish it twice and leave a caller guessing which copy the socket honours —
    and a browser could not use that copy anyway."""
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.connection.schemas import (  # noqa: E501
        Socket,
    )
    from agentclaw.community.core.engine_runtime.models import SocketInfo

    assert not hasattr(_build(_svc()).sockets[0], "headers")
    assert {f.name for f in dataclasses.fields(SocketInfo)} == {"kind", "url"}
    assert set(Socket.model_fields) == {"kind", "url"}


def test_result_exposes_no_field_beside_the_url_to_compose_with():
    """The target and the credential do travel inside the URL — a browser can
    carry them nowhere else. What the result must not hand over is the *pieces*:
    a separate target, connection type, or token field would let a caller build
    an address of its own, which is the hand-off this surface replaces."""
    result = _build(_svc())
    assert {f.name for f in dataclasses.fields(result)} == {
        "engine",
        "expires_at",
        "sockets",
    }
    assert {f.name for f in dataclasses.fields(result.sockets[0])} == {"kind", "url"}


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


def test_a_public_personal_bot_is_refused_before_a_device_is_touched():
    """``personal`` does not imply single-caller — ``ac_bots.public`` is free.

    ``bot_public_service`` sets the column with no ``bot_type`` gate, and
    ``ExpertChatService`` admits any caller to a public bot and creates their
    sessions on its binding. This socket's ``sessions.*`` methods would then
    reach those conversations, which is exactly what the service-bot refusal
    above exists to prevent.
    """
    bindings = _Bindings(raises=AssertionError("must not be reached"))
    svc = _svc(bots=_Bots(public="1"), bindings=bindings)

    with pytest.raises(EngineBotTypeNotSupportedError):
        _build(svc)


def test_a_collaborated_personal_bot_is_refused():
    """A coding app takes collaborators while staying ``bot_type='personal'``."""
    bindings = _Bindings(raises=AssertionError("must not be reached"))
    collaborators = _CollaboratorRepo({(BOT, OWNER): [{"user_id": "someone"}]})
    svc = _svc(bindings=bindings, collaborators=collaborators)

    with pytest.raises(EngineBotTypeNotSupportedError):
        _build(svc)


def test_an_unreadable_collaborator_table_refuses_rather_than_publishes():
    """The gate fails closed: a database blip must not open a shared bot."""

    class _Broken:
        def list_by_bot(self, bot_id, owner_id, env, role=None):
            raise RuntimeError("collaborator table unavailable")

    bindings = _Bindings(raises=AssertionError("must not be reached"))
    svc = _svc(bindings=bindings, collaborators=_Broken())

    with pytest.raises(EngineBotTypeNotSupportedError):
        _build(svc)


def test_the_collaborator_lookup_is_skipped_for_an_already_public_bot():
    """Public is decisive; there is nothing a second query could change."""
    collaborators = _CollaboratorRepo()
    svc = _svc(
        bots=_Bots(public="1"),
        bindings=_Bindings(raises=AssertionError("must not be reached")),
        collaborators=collaborators,
    )

    with pytest.raises(EngineBotTypeNotSupportedError):
        _build(svc)
    assert collaborators.calls == []


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
