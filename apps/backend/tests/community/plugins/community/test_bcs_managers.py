"""Unit tests for ``HttpBcsManagersPlugin`` — the self-signed BCS PUT adapter.

Transport-failure and wire-contract coverage for the PROD
``BcsManagersPlugin``: exactly one PUT per call to the BCS v1 managers
endpoint, authenticated with a self-signed ``X-Avernet-Principal`` (no
``Authorization`` header at all), every failure classified into the
kernel's ``DeliveryFailure`` vocabulary. All HTTP runs over
``httpx.MockTransport`` — no services, no sleeps; ``UNKNOWN``
classification comes from a handler that never answers. The test owns
the client it passes in; the plugin's self-owned lifecycle is exercised
separately.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import jwt
import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
    build_snapshot,
)
from agentclaw.community.core.gateway_principal import (
    PrincipalSignerConfig,
    PrincipalVerificationError,
    mint_service_app_principal,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    DeliveryFailure,
    ManagerSetChange,
    ManagerSnapshot,
    SyncFacts,
    SyncKey,
)
from agentclaw.community.plugin_api.bcs_managers import (
    BcsManagersError,
    BcsManagersPlugin,
)
from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Mode
from agentclaw.community.plugins.community.bcs_managers import (
    HttpBcsManagersPlugin,
)

_BASE_URL = "https://bcs.invalid"

#: A test signing key in the shape the minter test suite already uses —
#: never a real secret, and strong enough for HMAC-SHA256.
_MINT_KEY = "test-manager-sync-signing-key-0123456789abcdef"


#: The delivery minter the composition root would hand the plugin: this
#: component's own service identity under the fixed BCS envelope.
def _MINT_PRINCIPAL(tenant: str) -> str:
    return mint_service_app_principal(
        PrincipalSignerConfig(
            signing_key=_MINT_KEY, audience="bcs", issuer="backend", key_id="bare"
        ),
        tenant,
    )


@pytest.fixture
def snapshot() -> ManagerSnapshot:
    """Task 1's data construction — projected facts, no services."""
    facts = SyncFacts(
        SyncKey("tenant-a", "dev", 7),
        "bot",
        "entity",
        "owner",
        (CollaboratorFact("a", "admin", True), CollaboratorFact("b", "member", True)),
    )
    return build_snapshot(facts)


def _envelope(data: dict) -> dict:
    """A well-formed v1 success Envelope around ``data``."""
    return {"code": 20000, "message": "OK", "request_id": "req-test", "data": data}


@asynccontextmanager
async def _plugin_with(handler, *, base_url: str = _BASE_URL):
    """The plugin over a mock transport; the test owns and closes the client."""
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url=base_url, transport=transport) as client:
        yield HttpBcsManagersPlugin(
            base_url=base_url, mint_principal=_MINT_PRINCIPAL, client=client
        )


# — Step 1: the transport + wire-contract test ———————————————————————————


@pytest.mark.asyncio
async def test_put_carries_a_self_signed_principal_and_no_bearer():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "code": 20000,
                "message": "OK",
                "request_id": "req-test",
                "data": {"added": 2, "removed": 0, "total": 2},
            },
        )

    facts = SyncFacts(
        SyncKey("tenant-a", "dev", 7),
        "bot",
        "entity",
        "owner",
        (CollaboratorFact("a", "admin", True), CollaboratorFact("b", "member", True)),
    )
    async with httpx.AsyncClient(
        base_url=_BASE_URL, transport=httpx.MockTransport(handle)
    ) as client:
        plugin = HttpBcsManagersPlugin(
            base_url=_BASE_URL, client=client, mint_principal=_MINT_PRINCIPAL
        )
        result = await plugin.replace_managers(build_snapshot(facts), timeout_seconds=2)
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/api/v1/collaboration/bots/bot:entity/managers"
    assert json.loads(seen[0].content) == {"manager_user_ids": ["a", "b"]}
    # Direct delivery: no Bearer anywhere on the wire.
    assert "Authorization" not in seen[0].headers
    # The one credential is the self-signed BCS principal, and it is a
    # well-formed envelope under the test key: iss=backend, aud=bcs,
    # kid=bare, integer iat<exp, and the fixed service-app identity
    # scoped to the snapshot's own tenant.
    principal = seen[0].headers["X-Avernet-Principal"]
    header = jwt.get_unverified_header(principal)
    assert header["alg"] == "HS256"
    assert header["typ"] == "JWT"
    assert header["kid"] == "bare"
    claims = jwt.decode(
        principal,
        _MINT_KEY,
        algorithms=["HS256"],
        audience="bcs",
        issuer="backend",
    )
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    assert claims["exp"] > claims["iat"]
    (only,) = claims["principals"]
    assert only["type"] == "app"
    assert only["tenant"] == "tenant-a"
    assert only["app"]["app_name"] == "backend"
    assert only["app"]["tenant"] == "tenant-a"
    assert result.total == 2


# — Wire contract: empty roster, path encoding, payload passthrough ————


@pytest.mark.asyncio
async def test_empty_roster_puts_a_present_empty_array():
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=_envelope({"added": 0, "removed": 1, "total": 0})
        )

    facts = SyncFacts(
        SyncKey("tenant-a", "dev", 7),
        "bot",
        "entity",
        "owner",
        (CollaboratorFact("gone", "admin", False),),
    )
    projected = build_snapshot(facts)
    assert projected.manager_user_ids == ()
    async with _plugin_with(handle) as plugin:
        result = await plugin.replace_managers(projected, timeout_seconds=2)
    # The field is present and an array — never null, so BCS clears the set.
    assert json.loads(seen[0].content) == {"manager_user_ids": []}
    assert result.total == 0


@pytest.mark.asyncio
async def test_bot_id_special_chars_stay_one_encoded_segment():
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=_envelope({"added": 1, "removed": 0, "total": 1})
        )

    projected = ManagerSnapshot(
        key=SyncKey("tenant-a", "dev", 7),
        bcs_bot_id="bot x/y:z",
        owner_id="owner",
        manager_user_ids=("a",),
    )
    async with _plugin_with(handle) as plugin:
        await plugin.replace_managers(projected, timeout_seconds=2)
    # On the wire the id is percent-encoded as ONE segment: neither the
    # space, the slash, nor the colon may split or reshape the route.
    assert (
        seen[0].url.raw_path == b"/api/v1/collaboration/bots/bot%20x%2Fy%3Az/managers"
    )


@pytest.mark.asyncio
async def test_input_id_list_is_not_filtered():
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=_envelope({"added": 3, "removed": 0, "total": 3})
        )

    projected = ManagerSnapshot(
        key=SyncKey("tenant-a", "dev", 7),
        bcs_bot_id="bot:entity",
        owner_id="owner",
        manager_user_ids=("b", "a", "b"),
    )
    async with _plugin_with(handle) as plugin:
        await plugin.replace_managers(projected, timeout_seconds=2)
    # Verbatim: the adapter neither dedupes, sorts, nor prunes the list.
    assert json.loads(seen[0].content) == {"manager_user_ids": ["b", "a", "b"]}


# — Refusal to send + unknown passthrough ———————————————————————————————


@pytest.mark.asyncio
async def test_unmintable_principal_refuses_to_send():
    """A mint that fails (e.g. no signing key) is proven pre-send.

    The token would not verify at BCS, so the adapter refuses before the
    transport rather than sending a credential it knows is dead — the
    round is safely retryable once the deployment's key resolves.
    """

    def broken_mint(tenant: str) -> str:
        raise PrincipalVerificationError("no principal signing key is configured")

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=_envelope({"added": 0, "removed": 0, "total": 0})
        )

    facts = SyncFacts(
        SyncKey("tenant-a", "dev", 8),
        "bot",
        "entity",
        "owner",
        (CollaboratorFact("a", "admin", True),),
    )
    async with _plugin_with(handle) as _:
        plugin = HttpBcsManagersPlugin(
            base_url=_BASE_URL, mint_principal=broken_mint, client=None
        )
        try:
            with pytest.raises(BcsManagersError) as caught:
                await plugin.replace_managers(build_snapshot(facts), timeout_seconds=2)
        finally:
            await plugin.aclose()
    assert caught.value.kind == DeliveryFailure.NOT_SENT
    assert caught.value.code == "principal_mint_failed"
    assert seen == []  # nothing ever reached the transport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mint_result",
    [RuntimeError("signer unavailable"), ""],
    ids=["unexpected-exception", "empty-token"],
)
async def test_every_principal_mint_failure_is_classified_not_sent(
    snapshot: ManagerSnapshot, mint_result: object
):
    def broken_mint(tenant: str) -> str:
        if isinstance(mint_result, Exception):
            raise mint_result
        return mint_result

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=_envelope({"added": 0, "removed": 0, "total": 0})
        )

    async with httpx.AsyncClient(
        base_url=_BASE_URL, transport=httpx.MockTransport(handle)
    ) as client:
        plugin = HttpBcsManagersPlugin(
            base_url=_BASE_URL, mint_principal=broken_mint, client=client
        )
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=2)

    assert caught.value.kind is DeliveryFailure.NOT_SENT
    assert caught.value.code == "principal_mint_failed"
    assert seen == []


@pytest.mark.asyncio
async def test_total_zero_is_returned_verbatim(snapshot: ManagerSnapshot):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_envelope({"added": 0, "removed": 0, "total": 0})
        )

    async with _plugin_with(handle) as plugin:
        result = await plugin.replace_managers(snapshot, timeout_seconds=2)
    # The adapter never disguises a total=0 reply; drift is the
    # consumer's call.
    assert result == ManagerSetChange(
        added=0, removed=0, total=0, request_id="req-test"
    )


# — Error classification: NOT_SENT / UNKNOWN / REJECTED matrix ————————


@pytest.mark.asyncio
async def test_overall_timeout_is_unknown(snapshot: ManagerSnapshot):
    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()  # never answers; cancelled by the budget

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=0.05)
    # Pre- vs post-send is indistinguishable at the budget edge.
    assert caught.value.kind == DeliveryFailure.UNKNOWN
    assert caught.value.code == "timeout"


@pytest.mark.asyncio
async def test_attempt_budget_reaches_the_wire_not_httpx_defaults(
    snapshot: ManagerSnapshot,
):
    wire_timeouts: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        wire_timeouts.append(dict(request.extensions["timeout"]))
        return httpx.Response(
            200, json=_envelope({"added": 0, "removed": 0, "total": 2})
        )

    async with _plugin_with(handle) as plugin:
        result = await plugin.replace_managers(snapshot, timeout_seconds=30)
    assert result.total == 2
    # Above httpx's ~5s default phases: every wire phase carries the full
    # attempt budget. Before the per-request timeout this dict was
    # {connect: 5.0, read: 5.0, write: 5.0, pool: 5.0}.
    assert wire_timeouts == [{"connect": 30, "read": 30, "write": 30, "pool": 30}]


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ConnectError("connection refused"), "connect_error"),
        (httpx.ConnectTimeout("t"), "connect_timeout"),
        (httpx.PoolTimeout("t"), "pool_timeout"),
    ],
    ids=["connect_error", "connect_timeout", "pool_timeout"],
)
@pytest.mark.asyncio
async def test_pre_send_transport_failures_are_not_sent(
    snapshot: ManagerSnapshot, exc: BaseException, code: str
):
    def handle(request: httpx.Request) -> httpx.Response:
        raise exc

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=2)
    assert caught.value.kind == DeliveryFailure.NOT_SENT
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ReadTimeout("t"), "read_timeout"),
        (httpx.WriteTimeout("t"), "write_timeout"),
        (httpx.RemoteProtocolError("server disconnected"), "transport_error"),
    ],
    ids=["read_timeout", "write_timeout", "disconnect"],
)
@pytest.mark.asyncio
async def test_unverifiable_transport_failures_are_unknown(
    snapshot: ManagerSnapshot, exc: BaseException, code: str
):
    def handle(request: httpx.Request) -> httpx.Response:
        raise exc

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=2)
    # Nothing here proves the request did not reach BCS — never NOT_SENT.
    assert caught.value.kind == DeliveryFailure.UNKNOWN
    assert caught.value.code == code


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 413, 422])
@pytest.mark.asyncio
async def test_rejection_statuses_are_permanent(snapshot: ManagerSnapshot, status: int):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "code": status * 100,
                "message": "no",
                "request_id": "req-test",
                "data": {"error_code": "x"},
            },
        )

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=2)
    assert caught.value.kind == DeliveryFailure.REJECTED
    assert caught.value.code == f"http_{status}"


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503])
@pytest.mark.asyncio
async def test_unverifiable_statuses_are_unknown(
    snapshot: ManagerSnapshot, status: int
):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "code": 1000,
                "message": "no",
                "request_id": "req-test",
                "data": {},
            },
        )

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=2)
    # 429 included: no proof the limit applied before execution; the
    # adapter stops this round and never auto-retries.
    assert caught.value.kind == DeliveryFailure.UNKNOWN
    assert caught.value.code == f"http_{status}"


# — Envelope verification ——————————————————————————————————————————————


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[{"added": 0}]),
        httpx.Response(
            200,
            json={
                "code": 50000,
                "message": "OK",
                "request_id": "r",
                "data": {"added": 0, "removed": 0, "total": 0},
            },
        ),
        httpx.Response(
            200,
            json={
                "code": 20000,
                "message": "OK",
                "request_id": "r",
                "data": "not-a-dict",
            },
        ),
        httpx.Response(
            200,
            json={
                "code": 20000,
                "message": "OK",
                "request_id": "r",
                "data": {"added": True, "removed": 0, "total": 0},
            },
        ),
        httpx.Response(
            200,
            json={
                "code": 20000,
                "message": "OK",
                "request_id": "r",
                "data": {"added": "0", "removed": 0, "total": 0},
            },
        ),
        httpx.Response(
            200,
            json={
                "code": 20000,
                "message": "OK",
                "request_id": "r",
                "data": {"added": 0, "removed": 0, "total": -1},
            },
        ),
        httpx.Response(
            200,
            json={
                "code": 20000,
                "message": "OK",
                "request_id": "",
                "data": {"added": 0, "removed": 0, "total": 0},
            },
        ),
    ],
    ids=[
        "not-json",
        "payload-not-dict",
        "wrong-code",
        "data-not-dict",
        "bool-count",
        "string-count",
        "negative-count",
        "empty-request-id",
    ],
)
@pytest.mark.asyncio
async def test_malformed_envelopes_are_unknown(
    snapshot: ManagerSnapshot, response: httpx.Response
):
    def handle(request: httpx.Request) -> httpx.Response:
        return response

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=2)
    # An unverifiable reply is exactly that — never a success, never a
    # rejection guess.
    assert caught.value.kind == DeliveryFailure.UNKNOWN
    assert caught.value.code == "malformed_envelope"


# — Lifecycle + pairing —————————————————————————————————————————————————


@pytest.mark.asyncio
async def test_plugin_never_closes_a_caller_owned_client(
    snapshot: ManagerSnapshot,
):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=_envelope({"added": 0, "removed": 0, "total": 2})
        )

    async with httpx.AsyncClient(
        base_url=_BASE_URL, transport=httpx.MockTransport(handle)
    ) as client:
        plugin = HttpBcsManagersPlugin(
            base_url=_BASE_URL, mint_principal=_MINT_PRINCIPAL, client=client
        )
        await plugin.replace_managers(snapshot, timeout_seconds=2)
        await plugin.aclose()  # no-op: the caller owns this client
        assert not client.is_closed
        await plugin.replace_managers(snapshot, timeout_seconds=2)  # still usable
    assert len(seen) == 2  # the async with closed the caller's client, not us


@pytest.mark.asyncio
async def test_lazy_self_owned_client_is_created_and_closed(
    snapshot: ManagerSnapshot,
):
    plugin = HttpBcsManagersPlugin(
        base_url="http://127.0.0.1:1",  # nothing listens: connect refused, fast
        mint_principal=_MINT_PRINCIPAL,
    )
    assert plugin._client is None  # not born yet
    try:
        with pytest.raises(BcsManagersError) as caught:
            await plugin.replace_managers(snapshot, timeout_seconds=2)
        assert caught.value.kind == DeliveryFailure.NOT_SENT
        assert isinstance(plugin._client, httpx.AsyncClient)
        assert not plugin._client.is_closed
    finally:
        await plugin.aclose()
    assert plugin._client is None  # the self-owned pool was released


def test_registered_as_prod_impl_against_the_protocol():
    entries = [entry for entry in IMPL_REGISTRY if entry.cls is HttpBcsManagersPlugin]
    assert len(entries) == 1
    assert entries[0].mode is Mode.PROD
    assert entries[0].protocol is BcsManagersPlugin
    assert entries[0].flavor is None
