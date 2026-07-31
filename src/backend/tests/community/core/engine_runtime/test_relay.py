"""Unit tests for EngineRuntimeRelay (Track C, Task 3).

The relay is the only place Track C crosses into a device, so these cover the
four properties that must hold on every public runtime request: owner-scoped
bot resolution before any device work, single-point device resolution, engine
envelope normalisation, and transport-failure translation.
"""

from __future__ import annotations

import asyncio
import json
import threading

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
    EngineResourceNotFoundError,
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
        self.binding_calls: list[tuple[int, str, str]] = []

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

    def resolve_for_binding_invoke(self, binding_id, operator_id, *, bot_id,
                                   device_uuid=None):
        self.binding_calls.append((binding_id, operator_id, bot_id))
        if self._raises is not None:
            raise self._raises
        return DeviceContext(
            provider="baas",
            conn_info={"bind_id": binding_id},
            binding_id=binding_id,
            bot_id=bot_id,
            user_id=operator_id,
            bot_type="service",
        )


class _PublishRecord:
    """The few ``BotPublishRecord`` fields the relay reads."""

    def __init__(self, ext, status="success", record_id=7) -> None:
        self.ext = ext
        self.status = status
        self.id = record_id


class _PublishRepo:
    """Stands in for the publish repository's latest-success lookup."""

    def __init__(self, record: object | None = None) -> None:
        self._record = record
        self.calls: list[tuple[str, str]] = []

    def get_latest_success_by_source_bot_id(self, source_bot_id, env):
        self.calls.append((source_bot_id, env))
        return self._record


_UNSET = object()


class _Transport:
    def __init__(self, result: object = _UNSET, raises: Exception | None = None) -> None:
        # Sentinel, not ``None``: ``None`` is itself one of the malformed bodies
        # under test, so it cannot double as "use the default".
        self._result = {"success": True, "data": {}} if result is _UNSET else result
        self._raises = raises
        self.calls: list[tuple[str, str]] = []
        self.invocations: list[dict] = []

    async def invoke(self, conn_info, method, path, body=None, params=None, *, timeout=None):
        self.calls.append((method, path))
        # Record everything: a test that only checked (method, path) would pass
        # while _invoke silently dropped body / params / timeout — and timeout is
        # what the 504 path depends on.
        self.invocations.append(
            {
                "conn_info": conn_info,
                "method": method,
                "path": path,
                "body": body,
                "params": params,
                "timeout": timeout,
            }
        )
        if self._raises is not None:
            raise self._raises
        return self._result

    async def stream(self, *a, **k):  # pragma: no cover - unused here
        raise NotImplementedError


def _relay(
    bot_service=None, resolver=None, transport=None, publish_repo=None
) -> EngineRuntimeRelay:
    return EngineRuntimeRelay(
        bot_service or _BotService(),
        resolver or _Resolver(),
        transport or _Transport(),
        publish_repo or _PublishRepo(),
    )


def _service_bot_service() -> _BotService:
    return _BotService({(BOT, OWNER): {"bot_id": BOT, "bot_type": "service"}})


# ── isolation: bot resolution precedes any device work ────────────────────────


@pytest.mark.asyncio
async def test_foreign_bot_raises_before_touching_the_device():
    """The transport must never be invoked for a bot the caller does not own.

    A 404 alone would not prove this: the Track A guard constrains SQL, not a
    device call, so a relay that forwarded first and filtered after would still
    have reached someone else's device.
    """
    resolver, transport = _Resolver(), _Transport()
    # The default store holds BOT owned by OWNER; we ask as someone else, so this
    # exercises "exists but not yours" rather than the weaker "nothing exists".
    relay = _relay(_BotService(), resolver, transport)

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


@pytest.mark.asyncio
async def test_absent_total_is_none_not_zero():
    """Most engine list routes omit total; unknown is not empty."""
    result = await _relay(transport=_Transport({"success": True, "data": []})).call(
        bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
    )
    assert result.total is None


@pytest.mark.asyncio
async def test_engine_warning_text_never_escapes_the_relay():
    """The engine's caveat strings are internal prose, and some are not English.

    claude_code declares SESSION_CREATE limited with "teamclaw-aicoding-relay
    has no explicit sessions.create; OCB pre-allocates the sessionKey…";
    openclaw uses "通过 mcporter 命令启动". None of it may reach an external
    caller, so the relay logs it and carries nothing forward.
    """
    leak = "teamclaw-aicoding-relay has no explicit sessions.create"
    result = await _relay(
        transport=_Transport({"success": True, "data": {}, "warning": leak})
    ).call(bot_id=BOT, owner_id=OWNER, method="POST", path="/api/sessions")
    assert result.data == {}
    assert leak not in repr(result)


@pytest.mark.asyncio
async def test_success_false_inside_http_200_raises():
    """The engine reports business failure inside a 200; it must not pass."""
    transport = _Transport({"success": False, "message": "internal detail"})
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
async def test_missing_success_key_is_a_failure_on_enveloped_routes():
    """Only for routes that *do* use the envelope — see the raw-payload tests."""
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=_Transport({"data": {}})).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


# ── raw-payload routes (enveloped=False) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_raw_payload_route_returns_the_whole_body_as_data():
    """``GET /api/engine/status`` answers with no envelope at all.

    It returns ``EngineManager.status()`` directly — ``{engine,
    active_connections, process, transition}``, no ``success`` and no ``data``
    wrapper. Treating it as enveloped would fail every call against a perfectly
    healthy device.
    """
    status = {
        "engine": "openclaw",
        "active_connections": 2,
        "process": {"running": True},
        "transition": {},
    }
    result = await _relay(transport=_Transport(status)).call(
        bot_id=BOT,
        owner_id=OWNER,
        method="GET",
        path="/api/engine/status",
        enveloped=False,
    )
    assert result.data == status
    assert result.total is None


@pytest.mark.asyncio
async def test_raw_payload_mode_still_rejects_a_non_dict_body():
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=_Transport(result="nope")).call(
            bot_id=BOT,
            owner_id=OWNER,
            method="GET",
            path="/api/engine/status",
            enveloped=False,
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
async def test_engine_404_is_not_found_not_capability_unsupported():
    """The transport raises its not-found error for ANY adapter 404.

    The engine returns 404 for an unknown session id, an unknown model id, an
    unknown engine name — ordinary missing resources. Reporting those as
    "capability unsupported" would tell a caller polling a deleted session that
    its bot's engine lost the sessions capability. A capability the engine does
    not declare arrives as HTTP 501 instead, covered separately above.
    """
    transport = _Transport(raises=DeviceAdapterEndpointNotFoundError("no route"))
    with pytest.raises(EngineResourceNotFoundError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions/gone"
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
async def test_every_argument_is_forwarded_verbatim():
    """Covers body/params/timeout too — dropping any of them must fail a test."""
    transport = _Transport()
    await _relay(transport=transport).call(
        bot_id=BOT,
        owner_id=OWNER,
        method="DELETE",
        path="/api/sessions/abc/messages",
        body={"b": 1},
        params={"p": 2},
        timeout=12.5,
    )
    assert transport.invocations[0] == {
        "conn_info": {"url": "http://device"},
        "method": "DELETE",
        "path": "/api/sessions/abc/messages",
        "body": {"b": 1},
        "params": {"p": 2},
        "timeout": 12.5,
    }


@pytest.mark.asyncio
async def test_the_forward_targets_the_device_resolved_for_that_bot():
    """The isolation invariant is "the right device or no device".

    Asserting only that *a* call happened would pass even if the relay reused a
    stale or wrong conn_info, so pin the dialled connection to what the resolver
    returned for this bot.
    """

    class _OtherResolver(_Resolver):
        def resolve_for_bot(self, bot_id, user_id, *, device_uuid=None):
            ctx = super().resolve_for_bot(bot_id, user_id, device_uuid=device_uuid)
            return DeviceContext(
                provider=ctx.provider,
                conn_info={"url": f"http://device-for-{bot_id}"},
                binding_id=ctx.binding_id,
                bot_id=ctx.bot_id,
                user_id=ctx.user_id,
                bot_type=ctx.bot_type,
            )

    transport = _Transport()
    await _relay(resolver=_OtherResolver(), transport=transport).call(
        bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
    )
    assert transport.invocations[0]["conn_info"] == {"url": f"http://device-for-{BOT}"}


# ── resolve_bot returns a narrow value object ────────────────────────────────


def test_resolve_bot_does_not_hand_back_device_internals():
    """``get_bot`` attaches device_binding (device_id / provider / props).

    Handlers on a public surface built to stop publishing device topology must
    not be one ``envelope(bot)`` away from shipping it.
    """
    bot_service = _BotService(
        {
            (BOT, OWNER): {
                "bot_id": BOT,
                "bot_type": "personal",
                "active_engine": "openclaw",
                "device_binding": {"device_id": "secret", "device_props": {"x": 1}},
            }
        }
    )
    facts = _relay(bot_service).resolve_bot(BOT, OWNER)
    assert (facts.bot_id, facts.bot_type, facts.active_engine) == (
        BOT,
        "personal",
        "openclaw",
    )
    assert "secret" not in repr(facts)
    assert not hasattr(facts, "device_binding")


# ── transport failures outside the two named subclasses ──────────────────────


@pytest.mark.asyncio
async def test_bare_value_error_from_the_transport_is_an_upstream_error():
    """The transport contract documents a plain ValueError for HTTP failure.

    Letting it escape would answer 500 "Internal Server Error" for what is
    plainly an upstream problem.
    """
    transport = _Transport(raises=ValueError("connect failed"))
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
async def test_non_json_body_does_not_surface_as_a_config_error():
    """``httpx``'s ``.json()`` raises ``JSONDecodeError``, a ``ValueError``.

    That class is already mapped surface-wide to "Malformed engine
    configuration" — which would point a caller at their engine config when the
    real fault is a device returning a non-JSON body.
    """
    import json

    transport = _Transport(raises=json.JSONDecodeError("nope", "<<html>>", 0))
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET", path="/api/sessions"
        )


@pytest.mark.asyncio
async def test_a_failure_envelope_on_a_raw_route_still_fails():
    """A raw-payload route has no ``success`` key — but a transport can still
    answer one with a failure envelope.

    The community transport returns ``{"success": False, ...}`` for every call,
    so treating the raw branch as unconditionally successful made
    ``/api/engine/status`` answer 200 with empty defaults instead of surfacing
    the upstream failure.
    """
    transport = _Transport({"success": False, "message": "no device adapter"})
    with pytest.raises(EngineUpstreamError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, method="GET",
            path="/api/engine/status", enveloped=False,
        )


@pytest.mark.asyncio
async def test_a_raw_payload_that_merely_lacks_success_is_still_accepted():
    """The genuine raw shape has no ``success`` key; only an explicit False fails."""
    status = {"engine": "openclaw", "active_connections": 1}
    result = await _relay(transport=_Transport(status)).call(
        bot_id=BOT, owner_id=OWNER, method="GET",
        path="/api/engine/status", enveloped=False,
    )
    assert result.data == status


# ── service bots resolve through their published runtime binding ──────────────


@pytest.mark.asyncio
async def test_service_bot_resolves_through_its_published_binding():
    """A service bot's traffic must reach the *published* device.

    ``ac_bots.binding_id`` is the pre-publication draft — on the BaaS path the
    owner's own device, and the binding publishing produced is not on that
    column at all. Resolving by bot would send a caller's engine, model and
    approval calls to the wrong box while the bot they addressed runs elsewhere.
    """
    resolver = _Resolver()
    repo = _PublishRepo(_PublishRecord({"binding": {"online": 42, "verify": 41}}))
    relay = _relay(
        bot_service=_service_bot_service(), resolver=resolver, publish_repo=repo
    )

    await relay.call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/models")

    assert resolver.binding_calls == [(42, OWNER, BOT)]
    # The by-bot entry point — the draft binding — must not be consulted at all.
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_personal_bot_still_resolves_by_bot():
    """The published-binding lookup is service-only; personal bots are unchanged."""
    resolver = _Resolver()
    repo = _PublishRepo()
    await _relay(resolver=resolver, publish_repo=repo).call(
        bot_id=BOT, owner_id=OWNER, method="GET", path="/api/models"
    )

    assert resolver.calls == [(BOT, OWNER)]
    assert resolver.binding_calls == []
    # No publish lookup for a personal bot — it has no publish record to find.
    assert repo.calls == []


@pytest.mark.asyncio
async def test_service_bot_without_a_published_runtime_is_not_ready():
    """No published runtime is "not ready", never a fall back to the draft.

    Falling back would resolve the owner's own device — the defect this
    replaces — so an unpublished service bot gets the same retryable answer an
    unprovisioned personal bot gets, and the device is never touched.
    """
    transport = _Transport()
    with pytest.raises(EngineDeviceNotReadyError):
        await _relay(
            bot_service=_service_bot_service(),
            transport=transport,
            publish_repo=_PublishRepo(None),
        ).call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/models")

    assert transport.calls == []


@pytest.mark.asyncio
async def test_service_bot_with_no_stage_binding_is_not_ready():
    """A publish record whose ``ext.binding`` names no usable stage."""
    resolver = _Resolver()
    with pytest.raises(EngineDeviceNotReadyError):
        await _relay(
            bot_service=_service_bot_service(),
            resolver=resolver,
            publish_repo=_PublishRepo(_PublishRecord({"binding": {}})),
        ).call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/models")

    assert resolver.binding_calls == []


@pytest.mark.asyncio
async def test_publish_ext_is_read_when_it_arrives_as_json_text():
    """``ext`` is normally a parsed dict; the str form is handled the same."""
    resolver = _Resolver()
    repo = _PublishRepo(_PublishRecord(json.dumps({"binding": {"online": 9}})))
    await _relay(
        bot_service=_service_bot_service(), resolver=resolver, publish_repo=repo
    ).call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/models")

    assert resolver.binding_calls == [(9, OWNER, BOT)]


# ── device resolution stays off the event loop ────────────────────────────────


@pytest.mark.asyncio
async def test_device_resolution_does_not_block_the_event_loop():
    """Resolution is blocking network I/O and must run in a worker thread.

    A BaaS-backed bot resolves through ``BaasService.get_ws_info``, a sync
    ``httpx`` call with a 30-second timeout. Run inline, it parks the loop for
    that whole time and stalls every unrelated request on the worker.

    The assertion is that the loop keeps running *while* resolution is in
    flight: this coroutine is what releases the resolver, so if resolution held
    the loop, nothing could ever release it and the wait below would time out.
    """
    entered = threading.Event()
    release = threading.Event()

    class _BlockingResolver(_Resolver):
        def resolve_for_bot(self, bot_id, user_id, *, device_uuid=None):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("resolution was never released")
            return super().resolve_for_bot(bot_id, user_id, device_uuid=device_uuid)

    relay = _relay(resolver=_BlockingResolver())
    task = asyncio.create_task(
        relay.call(bot_id=BOT, owner_id=OWNER, method="GET", path="/api/models")
    )

    for _ in range(500):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set(), "resolution never started"

    release.set()
    result = await asyncio.wait_for(task, timeout=5)
    assert result.data == {}
