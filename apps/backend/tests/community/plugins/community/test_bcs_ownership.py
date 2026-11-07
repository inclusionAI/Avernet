"""Trusted ownership delivery wire, classification, and resource contracts."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import jwt
import pytest

from agentclaw.community.core.gateway_principal import (
    PrincipalSignerConfig,
    mint_service_app_principal,
)
from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure, SyncKey
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipSnapshot,
)
from agentclaw.community.plugin_api.bcs_ownership import (
    BcsOwnershipError,
    BcsOwnershipPlugin,
)
from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Mode
from agentclaw.community.plugins.community.bcs_ownership import HttpBcsOwnershipPlugin

_BASE_URL = "https://bcs.invalid"
_TEST_KEY = "test-ownership-signing-key-0123456789abcdef"


@pytest.fixture
def snapshot() -> OwnershipSnapshot:
    return OwnershipSnapshot(SyncKey("tenant-a", "dev", 7), "bot:stable", "B")


def _envelope(**changes) -> dict:
    data = {
        "bot_id": "bot:stable",
        "owner_user_id": "B",
        "previous_owner_user_id": "A",
        "changed": True,
    }
    data.update(changes)
    return {"code": 20000, "message": "OK", "request_id": "req-test", "data": data}


@asynccontextmanager
async def _plugin_with(handler, *, mint_principal=lambda tenant: "test-principal"):
    async with httpx.AsyncClient(
        base_url=_BASE_URL, transport=httpx.MockTransport(handler)
    ) as client:
        yield HttpBcsOwnershipPlugin(
            base_url=_BASE_URL, mint_principal=mint_principal, client=client
        )


@pytest.mark.asyncio
async def test_put_uses_encoded_stable_path_and_fresh_service_app_token(snapshot):
    seen, tenants = [], []

    def mint(tenant):
        tenants.append(tenant)
        return mint_service_app_principal(
            PrincipalSignerConfig(
                signing_key=_TEST_KEY, audience="bcs", issuer="backend", key_id="bare"
            ),
            tenant,
        )

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=_envelope())

    async with _plugin_with(handle, mint_principal=mint) as plugin:
        result = await plugin.replace_ownership(snapshot, timeout_seconds=2)
        await plugin.replace_ownership(
            replace(snapshot, key=SyncKey("tenant-b", "dev", 7)), timeout_seconds=2
        )
    assert tenants == ["tenant-a", "tenant-b"]
    assert len(seen) == 2
    assert result == OwnershipChange("bot:stable", "B", "A", True, "req-test")
    for request, tenant in zip(seen, tenants, strict=True):
        assert request.method == "PUT"
        assert (
            request.url.raw_path == b"/api/v1/collaboration/bots/bot%3Astable/ownership"
        )
        assert json.loads(request.content) == {"owner_user_id": "B"}
        assert "authorization" not in request.headers
        assert "x-avernet-user" not in request.headers
        principal = request.headers["X-Avernet-Principal"]
        header = jwt.get_unverified_header(principal)
        assert header == {"alg": "HS256", "kid": "bare", "typ": "JWT"}
        claims = jwt.decode(
            principal, _TEST_KEY, algorithms=["HS256"], audience="bcs", issuer="backend"
        )
        assert type(claims["iat"]) is int and type(claims["exp"]) is int
        assert claims["exp"] - claims["iat"] == 60
        (app,) = claims["principals"]
        assert app["type"] == "app"
        assert app["tenant"] == app["app"]["tenant"] == tenant
        assert app["app"]["app_name"] == "backend"
        assert "user" not in app and "user" not in claims


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_id", ["bot x/y:z", "bot?x#y%z"])
async def test_special_ids_are_one_encoded_segment(snapshot, bot_id):
    from urllib.parse import quote

    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=_envelope())

    async with _plugin_with(handle) as plugin:
        await plugin.replace_ownership(
            replace(snapshot, bcs_bot_id=bot_id), timeout_seconds=2
        )
    assert (
        seen[0].url.raw_path
        == (
            "/api/v1/collaboration/bots/" + quote(bot_id, safe="") + "/ownership"
        ).encode()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"owner_user_id": "B", "previous_owner_user_id": None, "changed": False},
        {"owner_user_id": None, "previous_owner_user_id": None, "changed": False},
        {"bot_id": "other:bot", "owner_user_id": "other-owner"},
    ],
    ids=["idempotent", "missing-bot-noop", "mismatch-left-to-core"],
)
async def test_returns_valid_results_without_target_alignment_policy(snapshot, data):
    body = _envelope(**data)
    async with _plugin_with(lambda request: httpx.Response(200, json=body)) as plugin:
        result = await plugin.replace_ownership(snapshot, timeout_seconds=2)
    assert result == OwnershipChange(**body["data"], request_id="req-test")


def _invalid_envelopes():
    cases = [("payload-array", []), ("payload-null", None)]
    for field, values in {
        "code": [None, True, False, 20000.0, "20000", 20001],
        "message": [None, 1, [], {}],
        "request_id": [None, "", " ", " r", "r\n", 7, True],
        "data": [None, [], "data", 1],
    }.items():
        for index, value in enumerate(values):
            body = _envelope()
            body[field] = value
            cases.append((f"{field}-{index}", body))
    for field in ("code", "message", "request_id", "data"):
        body = _envelope()
        del body[field]
        cases.append((f"missing-{field}", body))
    for field in ("bot_id", "owner_user_id", "previous_owner_user_id", "changed"):
        body = _envelope()
        del body["data"][field]
        cases.append((f"missing-{field}", body))
    for field, values in {
        "bot_id": [None, "", " ", " bot", "bot ", 1, True, []],
        "owner_user_id": ["", " ", " B", "B\n", 1, True, [], {}],
        "previous_owner_user_id": ["", " ", " A", "A\n", 1, False, [], {}],
        "changed": [None, 0, 1, "true", [], {}],
    }.items():
        for index, value in enumerate(values):
            body = _envelope(**{field: value})
            cases.append((f"{field}-{index}", body))
    for index, changes in enumerate(
        [
            {"owner_user_id": None},
            {"previous_owner_user_id": None},
            {"previous_owner_user_id": "B"},
            {"changed": False},
            {"owner_user_id": None, "changed": False},
            {"owner_user_id": None, "previous_owner_user_id": None},
        ]
    ):
        cases.append((f"contradiction-{index}", _envelope(**changes)))
    return [pytest.param(body, id=name) for name, body in cases]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", _invalid_envelopes())
async def test_malformed_envelope_is_unknown_with_no_payload_leak(snapshot, body):
    payload = copy.deepcopy(body)
    async with _plugin_with(
        lambda request: httpx.Response(200, json=payload)
    ) as plugin:
        with pytest.raises(BcsOwnershipError) as caught:
            await plugin.replace_ownership(snapshot, timeout_seconds=2)
    assert caught.value.kind is DeliveryFailure.UNKNOWN
    assert caught.value.code == "malformed_envelope"
    assert str(caught.value) == f"{DeliveryFailure.UNKNOWN.value} malformed_envelope"


@pytest.mark.asyncio
async def test_corrupt_json_is_unknown(snapshot):
    async with _plugin_with(
        lambda request: httpx.Response(200, content=b"sensitive-not-json")
    ) as plugin:
        with pytest.raises(BcsOwnershipError) as caught:
            await plugin.replace_ownership(snapshot, timeout_seconds=2)
    assert caught.value.kind is DeliveryFailure.UNKNOWN
    assert caught.value.code == "malformed_envelope"
    assert "sensitive" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        400,
        401,
        403,
        404,
        409,
        410,
        422,
        499,
        408,
        425,
        429,
        500,
        502,
        503,
        599,
        201,
        204,
        302,
    ],
)
async def test_status_classification_is_single_attempt_and_ignores_body(
    snapshot, status
):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(status, text="sensitive-refusal")

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsOwnershipError) as caught:
            await plugin.replace_ownership(snapshot, timeout_seconds=2)
    expected = (
        DeliveryFailure.REJECTED
        if 400 <= status < 500 and status not in {408, 425, 429}
        else DeliveryFailure.UNKNOWN
    )
    assert caught.value.kind is expected
    assert caught.value.code == f"http_{status}"
    assert str(caught.value) == f"{expected.value} http_{status}"
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mint_result",
    [RuntimeError("sensitive-key"), TimeoutError("sensitive"), "", None, 42, False],
)
async def test_mint_failures_are_not_sent(snapshot, mint_result):
    seen = []

    def mint(tenant):
        if isinstance(mint_result, Exception):
            raise mint_result
        return mint_result

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=_envelope())

    async with _plugin_with(handle, mint_principal=mint) as plugin:
        with pytest.raises(BcsOwnershipError) as caught:
            await plugin.replace_ownership(snapshot, timeout_seconds=2)
    assert caught.value.kind is DeliveryFailure.NOT_SENT
    assert caught.value.code == "principal_mint_failed"
    assert "sensitive" not in str(caught.value)
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, kind, code",
    [
        (httpx.ConnectError, DeliveryFailure.NOT_SENT, "connect_error"),
        (httpx.ConnectTimeout, DeliveryFailure.NOT_SENT, "connect_timeout"),
        (httpx.PoolTimeout, DeliveryFailure.NOT_SENT, "pool_timeout"),
        (httpx.ReadTimeout, DeliveryFailure.UNKNOWN, "read_timeout"),
        (httpx.WriteTimeout, DeliveryFailure.UNKNOWN, "write_timeout"),
        (httpx.TimeoutException, DeliveryFailure.UNKNOWN, "transport_error"),
        (httpx.ReadError, DeliveryFailure.UNKNOWN, "transport_error"),
        (httpx.WriteError, DeliveryFailure.UNKNOWN, "transport_error"),
        (httpx.RemoteProtocolError, DeliveryFailure.UNKNOWN, "transport_error"),
        (httpx.HTTPError, DeliveryFailure.UNKNOWN, "transport_error"),
        (TimeoutError, DeliveryFailure.UNKNOWN, "timeout"),
    ],
)
async def test_transport_failure_codes_without_retries_or_details(
    snapshot, error, kind, code
):
    seen = []

    def handle(request):
        seen.append(request)
        raise error("sensitive-transport-detail")

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsOwnershipError) as caught:
            await plugin.replace_ownership(snapshot, timeout_seconds=2)
    assert caught.value.kind is kind
    assert caught.value.code == code
    assert str(caught.value) == f"{kind.value} {code}"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_outer_attempt_deadline_is_unknown(snapshot):
    async def handle(request):
        await asyncio.Event().wait()

    async with _plugin_with(handle) as plugin:
        with pytest.raises(BcsOwnershipError) as caught:
            await plugin.replace_ownership(snapshot, timeout_seconds=0.02)
    assert caught.value.kind is DeliveryFailure.UNKNOWN
    assert caught.value.code == "timeout"


@pytest.mark.asyncio
async def test_remaining_budget_reaches_every_http_phase(snapshot):
    timeouts = []

    def mint(tenant):
        time.sleep(0.02)  # Synchronous signing/setup still consumes the attempt budget.
        return "test-principal"

    def handle(request):
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json=_envelope())

    async with _plugin_with(handle, mint_principal=mint) as plugin:
        await plugin.replace_ownership(snapshot, timeout_seconds=30)
    (phases,) = timeouts
    assert set(phases) == {"connect", "read", "write", "pool"}
    assert len(set(phases.values())) == 1
    assert all(0 < remaining <= 29.98 for remaining in phases.values())


@pytest.mark.asyncio
async def test_cancellation_propagates(snapshot):
    entered = asyncio.Event()

    async def handle(request):
        entered.set()
        await asyncio.Event().wait()

    async with _plugin_with(handle) as plugin:
        task = asyncio.create_task(
            plugin.replace_ownership(snapshot, timeout_seconds=30)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_minter_cancellation_propagates_without_send(snapshot):
    def mint(tenant):
        raise asyncio.CancelledError

    async with _plugin_with(
        lambda request: pytest.fail("must not send"), mint_principal=mint
    ) as plugin:
        with pytest.raises(asyncio.CancelledError):
            await plugin.replace_ownership(snapshot, timeout_seconds=2)


@pytest.mark.asyncio
async def test_caller_owned_client_is_never_closed(snapshot):
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_envelope())
        ),
    ) as client:
        plugin = HttpBcsOwnershipPlugin(
            base_url=_BASE_URL, mint_principal=lambda t: "test", client=client
        )
        await plugin.replace_ownership(snapshot, timeout_seconds=2)
        await plugin.aclose()
        await plugin.aclose()
        assert not client.is_closed
        await plugin.replace_ownership(snapshot, timeout_seconds=2)


@pytest.mark.asyncio
async def test_owned_pool_is_lazy_reused_and_closed_exactly_once(snapshot, monkeypatch):
    import agentclaw.community.plugins.community.bcs_ownership as module

    created = []
    real_client = httpx.AsyncClient

    class CountingTransport(httpx.MockTransport):
        closes = 0

        async def aclose(self):
            self.closes += 1
            await super().aclose()

    transport = CountingTransport(lambda request: httpx.Response(200, json=_envelope()))

    def make_client(**kwargs):
        client = real_client(transport=transport, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(module.httpx, "AsyncClient", make_client)
    plugin = HttpBcsOwnershipPlugin(base_url=_BASE_URL, mint_principal=lambda t: "test")
    assert created == []
    await plugin.aclose()
    await plugin.replace_ownership(snapshot, timeout_seconds=2)
    await plugin.replace_ownership(snapshot, timeout_seconds=2)
    assert len(created) == 1 and not created[0].is_closed
    assert str(created[0].base_url) == _BASE_URL
    await plugin.aclose()
    await plugin.aclose()
    assert created[0].is_closed and transport.closes == 1


def test_registered_as_prod_ownership_plugin():
    (entry,) = [entry for entry in IMPL_REGISTRY if entry.cls is HttpBcsOwnershipPlugin]
    assert entry.protocol is BcsOwnershipPlugin
    assert entry.mode is Mode.PROD and entry.flavor is None
