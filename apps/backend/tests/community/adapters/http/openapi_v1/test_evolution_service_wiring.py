"""Hermetic Backend -> signed receipt -> real Evolution routes/store.

Only repositories/platform providers are synthetic. No employee identity, live
login, network, consumer, Run submission, sandbox or promotion is exercised.
"""

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module

from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.di.evolution_control_config import (
    EvolutionControlClientFactory,
    EvolutionControlConfig,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    reset_principal_verifier_config_cache,
)
from avernet_evolution.adapters.backend_access import HEADER, BackendAccess, fingerprint
from avernet_evolution.adapters.control import build_control_router, install_errors
from avernet_evolution.composition import compose
from avernet_evolution.config import Settings
from avernet_evolution.local import BOT as FIXTURE_BOT
from avernet_evolution.local import STRATEGY, build_local_platform

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    public_router,
)
from tests.community.adapters.http.openapi_v1.test_bot_access import (
    BOT,
    CALLER,
    OWNER,
    _Audit,
    _Locks,
)
from tests.community.adapters.http.openapi_v1.test_evolution import (
    RESOURCE,
    URL,
    Bots,
    Collaborators,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    KEY,
    boot_with_key,
    mint,
)

BINDING = {
    "id": "wiring-only",
    "strategy": STRATEGY,
    "trigger": {"manual": True},
    "parent": "active",
    "allowed_genes": ["persona"],
    "verification_profile": "pending@1",
    "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
    "auto_promote_ceiling": None,
}


@asynccontextmanager
async def wired(tmp_path, *, receiver_key=KEY, receiver_tenant=DEFAULT_AVERNET_TENANT):
    worker_key, token = tmp_path / "worker-key", tmp_path / "local-token"
    worker_key.write_text("synthetic-worker-signing-material-at-least-32-bytes")
    token.write_text("synthetic-local-fixture-token-at-least-32-bytes")
    settings = Settings(
        database=tmp_path / "control.db",
        signing_key_file=worker_key,
        local_token_file=token,
        consumer=False,
    )
    boot_with_key(KEY)
    runtime = await compose(settings, await build_local_platform(settings))
    access = BackendAccess(key=KEY, tenant=DEFAULT_AVERNET_TENANT)
    calls = []
    service = FastAPI()
    install_errors(service)
    service.include_router(
        build_control_router(
            runtime,
            backend_access=BackendAccess(key=receiver_key, tenant=receiver_tenant),
        )
    )

    class RecordingTransport(httpx.ASGITransport):
        async def handle_async_request(self, request):
            grant = access.verify(
                request.headers[HEADER],
                bot_id=BOT,
                owner_hint=RESOURCE,
                request_hash=fingerprint(
                    request.method,
                    request.url.path,
                    request.url.query.decode(),
                    request.content,
                    request.headers,
                ),
            )
            calls.append((request, grant))
            return await super().handle_async_request(request)

    class Clients(EvolutionControlClientFactory):
        def open(self):
            return httpx.AsyncClient(
                base_url=self.config.upstream,
                transport=RecordingTransport(service),
                trust_env=False,
                follow_redirects=False,
            )

    config = EvolutionControlConfig(upstream="http://evolution.test")
    collaborators, audit = Collaborators(PermissionLevel.MEMBER), _Audit()

    class Wiring(Module):
        def configure(self, binder):
            for interface, value in (
                (BotRepository, Bots()),
                (CollaboratorServiceProtocol, collaborators),
                (BotCollabLogRepositoryProtocol, audit),
                (CollaboratorLockServiceProtocol, _Locks()),
                (EvolutionControlConfig, config),
                (EvolutionControlClientFactory, Clients(config)),
            ):
                binder.bind(interface, to=InstanceProvider(value))

    backend = FastAPI()
    backend.include_router(public_router())
    mount_public_error_handlers(backend)
    attach_injector(backend, Injector([Wiring()]))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(backend),
            base_url="http://backend.test",
            headers={"X-Avernet-Principal": mint(user_id=CALLER, tenant=None)},
            params={"user_id": CALLER, "entity_id": RESOURCE},
        ) as client:
            yield client, runtime, calls, collaborators
    finally:
        await runtime.close()
        reset_principal_verifier_config_cache()


@pytest.mark.asyncio
async def test_catalog_bindings_cas_actor_and_persistence_without_execution(tmp_path):
    async with wired(tmp_path) as (client, runtime, calls, _):
        active = await runtime.platform.genome.resolve_revision(FIXTURE_BOT, "active")
        catalog = await client.get(URL + "/strategies")
        assert catalog.status_code == 200
        assert catalog.json()["data"]["strategies"][0]["id"] == STRATEGY.split("@")[0]
        initial = await client.get(
            URL + "/bindings",
            headers={
                "Cookie": "IAM_TOKEN=synthetic-not-forwarded",
                "Authorization": "Bearer synthetic-not-forwarded",
                HEADER: "synthetic-client-forgery",
            },
        )
        assert initial.status_code == 200
        assert initial.headers["etag"] == '""'
        assert initial.json()["data"]["bindings"] == []
        payload = {"bindings": [BINDING]}
        assert (await client.put(URL + "/bindings", json=payload)).status_code == 428
        saved = await client.put(
            URL + "/bindings",
            json=payload,
            headers={"If-Match": initial.headers["etag"]},
        )
        assert saved.status_code == 200, saved.text
        tag = saved.headers["etag"]
        assert tag != initial.headers["etag"]
        assert (
            await client.put(
                URL + "/bindings",
                json=payload,
                headers={"If-Match": '"stale"'},
            )
        ).status_code == 412
        read = await client.get(URL + "/bindings")
        assert read.headers["etag"] == tag
        assert read.json()["data"]["bindings"][0]["id"] == BINDING["id"]
        stored = runtime.store.get_bindings(OWNER, BOT)
        assert stored["updated_by"] == CALLER != OWNER
        for request, grant in calls:
            assert grant.actor == CALLER
            assert grant.bot.owner_id == OWNER
            assert grant.bot.entity_id == RESOURCE
            assert dict(request.url.params) == {"entity_id": RESOURCE}
            assert "cookie" not in request.headers
            assert "authorization" not in request.headers
            assert "x-avernet-principal" not in request.headers
        assert runtime.runner._task is None
        for table in ("evolution_run", "evolution_job", "evolution_run_candidate"):
            assert (
                runtime.store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                    0
                ]
                == 0
            )
        assert (
            await runtime.platform.genome.resolve_revision(FIXTURE_BOT, "active")
        ).revision.id == active.revision.id
    # New runtime and connections, same persistent state; no consumer starts.
    async with wired(tmp_path) as (client, runtime, _, _):
        read = await client.get(URL + "/bindings")
        assert read.status_code == 200 and read.headers["etag"] == tag
        assert read.json()["data"]["bindings"][0]["id"] == BINDING["id"]
        assert runtime.store.get_bindings(OWNER, BOT)["updated_by"] == CALLER


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status", [("login", 401), ("permission", 404), ("identity", 403)]
)
async def test_shared_gate_denies_before_real_service(tmp_path, failure, status):
    async with wired(tmp_path) as (client, runtime, calls, collaborators):
        if failure == "login":
            del client.headers["X-Avernet-Principal"]
        elif failure == "permission":
            collaborators.level = PermissionLevel.NONE
        else:
            client.params = {"user_id": OWNER, "entity_id": RESOURCE}
        assert (await client.get(URL + "/bindings")).status_code == status
        assert calls == []
        assert runtime.store.get_bindings(OWNER, BOT) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["key", "tenant"])
async def test_real_receiver_refuses_mismatched_service_trust(tmp_path, mismatch):
    overrides = (
        {"receiver_key": "different-synthetic-platform-key-at-least-32-bytes"}
        if mismatch == "key"
        else {"receiver_tenant": "other-fixture-tenant"}
    )
    async with wired(tmp_path, **overrides) as (client, runtime, calls, _):
        result = await client.get(URL + "/bindings")
        assert result.status_code == 403
        assert result.json()["code"] == 403000
        assert len(calls) == 1  # Backend gate passed; the real receiver rejected it.
        assert runtime.store.get_bindings(OWNER, BOT) is None
