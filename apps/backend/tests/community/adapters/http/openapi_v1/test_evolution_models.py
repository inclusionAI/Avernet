"""Trusted Bot model bridge: real ASGI auth/current permission and Run scope."""

import json

import pytest
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.engine_runtime.models import EngineResult
from avernet_evolution.core.evolution_run.data_scope import (
    DataScope,
    current_data_scope,
    data_scope,
)
from avernet_evolution.core.evolution_strategy import registration_from_json
from avernet_evolution.plugins.model_http import HttpBotModels
from avernet_evolution.plugins.platform_http import PlatformHttpError
from avernet_genome.models import BotRef

from tests.community.adapters.http.openapi_v1.test_evolution_service_wiring import (
    BINDING,
    BOT,
    CALLER,
    OWNER,
    RESOURCE,
    URL,
    SessionRelay,
    wired,
)
from tests.community.core.bot_genome.example_blobs import SUPPORT_BOT, spec_of
from tests.community.core.bot_genome.service_fixtures import World
from tests.community.core.bot_genome.test_capture import FakeReader

REF = "fixture/model-probe@1.0.0"
BOT_REF = BotRef(RESOURCE, BOT, OWNER, "openclaw", "personal", "staff")
SCOPE = DataScope(BOT_REF, CALLER, "run-1", "parent-1", REF)


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    response = {"text": "answer", "model": "fixture/current", "tokens": 12}

    async def complete(self, **kwargs):
        calls.append(kwargs)
        assert kwargs["path"] == "/api/engine/models:complete"
        assert (
            kwargs["bot_id"],
            kwargs["owner_id"],
            kwargs["stage"],
            kwargs["method"],
        ) == (BOT, RESOURCE, "draft", "POST")
        assert kwargs["timeout"] == 200
        return EngineResult(dict(response))

    monkeypatch.setattr(SessionRelay, "call", complete)
    return calls, response


def make_world(tmp_path):
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    return world


async def setup_models(platform):
    platform.models = HttpBotModels(
        platform.genome.http, stage="draft", call_charge_usd=0.05
    )
    await platform.registry.register(
        registration_from_json(
            {
                "id": "fixture/model-probe",
                "version": "1.0.0",
                "runtime": {"kind": "in_process", "package": "fixture"},
                "needs": {"models@1": {}},
            }
        ),
        {},
    )


@pytest.mark.asyncio
async def test_persisted_run_operator_round_trip_budget_and_revocation(
    tmp_path, upstream
):
    calls, _ = upstream
    async with wired(
        tmp_path, world=make_world(tmp_path), extend_platform=setup_models
    ) as (
        client,
        runtime,
        _,
        collabs,
        _,
        callbacks,
        _,
    ):
        collabs.level = PermissionLevel.ADMIN
        initialized = await client.post(
            f"/openapi/v1/bots/{BOT}/genome/baseline:initialize"
        )
        assert initialized.status_code == 201, initialized.text
        current = await client.get(URL + "/bindings")
        saved = await client.put(
            URL + "/bindings",
            json={"bindings": [{**BINDING, "strategy": REF}]},
            headers={"If-Match": current.headers["etag"]},
        )
        assert saved.status_code == 200, saved.text
        queued = await client.post(
            URL + "/runs",
            json={"binding_id": BINDING["id"]},
            headers={"Idempotency-Key": "model-probe"},
        )
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["data"]["run"]
        token = runtime.channel.tokens.issue("model-worker")
        job = await runtime.channel.claim("model-worker", token, [REF])
        reply = await runtime.channel.models_complete(
            job["job_id"],
            job["fencing_token"],
            messages=[{"role": "user", "content": "probe"}],
        )
        assert reply == {"text": "answer", "model": "fixture/current", "cost_usd": 0.05}
        assert len(calls) == 1 and current_data_scope.get() is None
        run = runtime.store.get_run(run_id)
        assert run.trigger.operator_user_id == CALLER
        assert run.budget_used.tokens == 12 and float(run.budget_used.usd) == 0.05
        request = callbacks[-1]
        assert request.url.path.endswith("/models:complete")
        assert set(json.loads(request.content)) == {"messages", "model", "max_tokens"}
        collabs.level = (
            PermissionLevel.MEMBER
        )  # queued Run is not a durable permission grant
        with pytest.raises(PermissionError):
            await runtime.channel.models_complete(
                job["job_id"],
                job["fencing_token"],
                messages=[{"role": "user", "content": "probe"}],
            )
        assert len(calls) == 1
        assert float(runtime.store.get_run(run_id).budget_used.usd) == 0.05


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["missing-run", "owner-changed", "member"])
async def test_callback_scope_and_current_owner_refusals(tmp_path, upstream, refusal):
    calls, _ = upstream
    async with wired(tmp_path, world=make_world(tmp_path)) as (
        _,
        runtime,
        _,
        collabs,
        bots,
        _,
        _,
    ):
        collabs.level = PermissionLevel.ADMIN
        scope = SCOPE
        if refusal == "missing-run":
            scope = DataScope(BOT_REF, CALLER)
        elif refusal == "owner-changed":
            bots.current_owner = "new-owner"
        else:
            collabs.level = PermissionLevel.MEMBER
        models = HttpBotModels(
            runtime.platform.genome.http, stage="draft", call_charge_usd=0.05
        )
        with data_scope(scope), pytest.raises(PermissionError):
            # Missing scope is caught before HTTP; others are rechecked by Backend.
            await models.complete(messages=[{"role": "user", "content": "probe"}])
        assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"api_key": "fixture-secret"},
        {"base_url": "https://untrusted.example"},
        {"max_tokens": True},
        {"messages": []},
    ],
)
async def test_backend_refuses_extra_credentials_or_malformed_input(
    tmp_path, upstream, changes
):
    calls, _ = upstream
    async with wired(tmp_path, world=make_world(tmp_path)) as (
        _,
        runtime,
        _,
        collabs,
        _,
        _,
        _,
    ):
        collabs.level = PermissionLevel.ADMIN
        with data_scope(SCOPE), pytest.raises(PlatformHttpError) as exc:
            await runtime.platform.genome.http.request(
                BOT_REF,
                "POST",
                "/models:complete",
                body={"messages": [{"role": "user", "content": "probe"}], **changes},
            )
        assert exc.value.status == 422 and "secret" not in str(exc.value)
        assert not calls


@pytest.mark.asyncio
async def test_backend_refuses_secret_bearing_engine_response(tmp_path, upstream):
    _, response = upstream
    response["api_key"] = "fixture-secret"
    async with wired(tmp_path, world=make_world(tmp_path)) as (
        _,
        runtime,
        _,
        collabs,
        _,
        _,
        _,
    ):
        collabs.level = PermissionLevel.ADMIN
        with data_scope(SCOPE), pytest.raises(PlatformHttpError) as exc:
            await HttpBotModels(
                runtime.platform.genome.http, stage="draft", call_charge_usd=0.05
            ).complete(messages=[{"role": "user", "content": "probe"}])
        assert exc.value.status == 502 and "secret" not in str(exc.value)
