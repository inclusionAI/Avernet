"""Bot inference adapter: signed scope, pinned override, bounded safe wire."""

import json

import httpx
import pytest

from avernet_evolution.adapters.backend_data import HEADER, verify_data_proof
from avernet_evolution.core.evolution_run.data_scope import DataScope, data_scope
from avernet_evolution.local import BOT
from avernet_evolution.plugins.model_http import HttpBotModels
from avernet_evolution.plugins.platform_http import BotHttp, PlatformHttpError

KEY = "fixture-model-signing-key-32-bytes-minimum"
SCOPE = DataScope(BOT, "real-operator", "run-1", "parent-1", "fixture/strategy@1")


@pytest.fixture
def model_bridge():
    requests = []
    response = {"text": "answer", "model": "fixture/current", "tokens": 14}

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 200000, "data": response})

    async def no_credentials(bot):
        raise AssertionError("must not resolve app/browser credentials")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    bridge = BotHttp(
        client, no_credentials, data_origin="http://backend.test", data_key=KEY, data_tenant="fixture"
    )
    return client, bridge, requests, response


async def test_signed_run_scope_and_long_model_timeout(model_bridge):
    client, bridge, requests, _ = model_bridge
    try:
        models = HttpBotModels(bridge, stage="draft", call_charge_usd=0.05)
        with data_scope(SCOPE):
            result = await models.complete(messages=[{"role": "user", "content": "probe"}])
        assert (result.text, result.model, result.tokens, result.cost_usd) == (
            "answer",
            "fixture/current",
            14,
            0.05,
        )
        request = requests[0]
        proof = verify_data_proof(KEY, request.headers[HEADER])
        assert (proof.bot_id, proof.user_id, proof.run, proof.parent, proof.strategy) == (
            BOT.bot_id,
            "real-operator",
            "run-1",
            "parent-1",
            "fixture/strategy@1",
        )
        assert dict(request.url.params) == {"stage": "draft"}
        assert request.extensions["timeout"]["read"] == 210
        assert not {"authorization", "cookie"} & set(request.headers)
        assert json.loads(request.content) == {
            "messages": [{"role": "user", "content": "probe"}],
            "model": None,
            "max_tokens": 4096,
        }
        await models.close()
        assert not client.is_closed  # shared client owned by assembly
    finally:
        await client.aclose()


@pytest.mark.parametrize("scope", [None, DataScope(BOT, "operator")])
async def test_missing_verified_run_refused(model_bridge, scope):
    client, bridge, requests, _ = model_bridge
    try:
        with data_scope(scope), pytest.raises(PermissionError):
            await HttpBotModels(bridge, stage="draft", call_charge_usd=0.05).complete(messages=[])
        assert not requests
    finally:
        await client.aclose()


async def test_manual_bot_model_override_is_pinned(model_bridge):
    client, bridge, requests, response = model_bridge
    try:
        response["model"] = "fixture/override"
        models = HttpBotModels(bridge, stage="online", call_charge_usd=0.01, model="fixture/override")
        with data_scope(SCOPE):
            result = await models.complete(messages=[{"role": "system", "content": "probe"}])
            assert result.model == "fixture/override"
            with pytest.raises(ValueError, match="override"):
                await models.complete(messages=[], model="fixture/other")
        assert len(requests) == 1 and json.loads(requests[0].content)["model"] == "fixture/override"
    finally:
        await client.aclose()


@pytest.mark.parametrize("changes", [{"api_key": "secret"}, {"tokens": True}, {"text": ""}, {"model": 123}])
async def test_response_strict_and_sanitized(model_bridge, changes):
    client, bridge, _, response = model_bridge
    try:
        response.update(changes)
        with data_scope(SCOPE), pytest.raises(PlatformHttpError, match="invalid Bot model") as exc:
            await HttpBotModels(bridge, stage="draft", call_charge_usd=0.05).complete(
                messages=[{"role": "user", "content": "probe"}]
            )
        assert "secret" not in str(exc.value)
    finally:
        await client.aclose()


@pytest.mark.parametrize("charge", [0, -1, 0.51, float("nan"), float("inf")])
def test_charge_validation(charge):
    with pytest.raises(ValueError):
        HttpBotModels(None, stage="draft", call_charge_usd=charge)
