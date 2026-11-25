"""Hermetic Backend/Evolution ASGI seams; no live identity, network or sandbox."""

from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module

from agentclaw.community.adapters.http.openapi_v1.evolution_data import router as data_router
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_genome.models import (
    BotRef,
    Provenance,
    ProvenanceKind,
    RefMover,
    RefUpdate,
    RevisionStatus,
)
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.core.engine_runtime.session_key import PassThroughSessionKeyCodec, SessionKeyCodecRegistry
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
from avernet_evolution.adapters.backend_data import HEADER as DATA_HEADER
from avernet_evolution.adapters.backend_data import PREFIX as DATA_PREFIX
from avernet_evolution.adapters.backend_data import issue_data_proof
from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_run.data_scope import run_data_scope
from avernet_evolution.core.evolution_run.ports import BotPolicy
from avernet_evolution.config import Settings
from avernet_evolution.local import BOT as FIXTURE_BOT
from avernet_evolution.local import STRATEGY, LocalGovernance, build_local_platform
from avernet_evolution.plugin_api import Platform
from avernet_evolution.plugins.platform_http import BotHttp, HttpGenome
from avernet_evolution.plugins.session_http import HttpSessionExperience
from avernet_evolution.plugins.verifiers.rules import RULE_VALIDATION_MARKERS, build_rule_verifier

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

from tests.community.core.bot_genome.example_blobs import SUPPORT_BOT, spec_of
from tests.community.core.bot_genome.service_fixtures import World
from tests.community.core.bot_genome.test_capture import FakeReader

BINDING = {
    "id": "wiring-only",
    "strategy": STRATEGY,
    "trigger": {"manual": True},
    "parent": "active",
    "allowed_genes": ["persona"],
    "verification_profile": "rules-file@1",
    "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
    "auto_promote_ceiling": None,
}


class LiveBots(Bots):
    current_owner = OWNER

    def get_by_id_and_entity(self, bot_id, entity_id):
        matches = self.list_matches_by_legacy_locator(bot_id, entity_id)
        return {**matches[0], "owner_id": self.current_owner} if matches else None


class GenomeBotService:
    """The genome router's bot facts: the addressed bot, as the registry needs it."""

    def get_bot(self, bot_id, user_id):
        assert bot_id == BOT
        return {
            "bot_id": BOT,
            "entity_id": RESOURCE,
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
        }


class SessionRelay:
    async def resolve_bot_off_loop(self, bot_id, owner_id, caller_id):
        assert (bot_id, owner_id, caller_id) == (BOT, RESOURCE, CALLER)
        return BotFacts(BOT, "personal", "openclaw", OWNER)

    async def call(self, *, bot_id, owner_id, stage, method, path, facts, params):
        assert (bot_id, owner_id, stage, method) == (BOT, RESOURCE, "draft", "GET")
        now = datetime.now(UTC).isoformat()
        if path == "/api/sessions":
            assert params == {"limit": 21, "offset": 0}
            return EngineResult([{"id": "test-session", "gmt_modified": now}])
        assert path == "/api/sessions/test-session/messages"
        return EngineResult([{"id": "test-message", "role": "user", "content": "fixture message", "gmt_created": now}])


@asynccontextmanager
async def wired(tmp_path, *, receiver_key=KEY, receiver_tenant=DEFAULT_AVERNET_TENANT, world=None):
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
    calls, callbacks = [], []
    access = BackendAccess(key=KEY, tenant=DEFAULT_AVERNET_TENANT)
    service, backend = FastAPI(), FastAPI()
    install_errors(service)
    backend.include_router(public_router())
    mount_public_error_handlers(backend)
    config = EvolutionControlConfig(
        upstream="http://evolution.test", builtin_data_tenant=DEFAULT_AVERNET_TENANT if world else "",
    )
    collaborators, audit, bots = Collaborators(PermissionLevel.MEMBER), _Audit(), LiveBots()

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

    class Wiring(Module):
        def configure(self, binder):
            entries = [
                (BotRepository, bots),
                (CollaboratorServiceProtocol, collaborators),
                (BotCollabLogRepositoryProtocol, audit),
                (CollaboratorLockServiceProtocol, _Locks()),
                (EvolutionControlConfig, config),
                (EvolutionControlClientFactory, Clients(config)),
            ]
            if world is not None:
                entries.extend([
                    (GenomeRegistryProtocol, world.registry),
                    (BotServiceProtocol, GenomeBotService()),
                    (EngineRuntimeRelayProtocol, SessionRelay()),
                    (SessionKeyCodecRegistry, SessionKeyCodecRegistry(default=PassThroughSessionKeyCodec())),
                ])
            for interface, value in entries:
                binder.bind(interface, to=InstanceProvider(value))

    if world is not None:
        backend.include_router(data_router)
    attach_injector(backend, Injector([Wiring()]))

    class CallbackTransport(httpx.ASGITransport):
        async def handle_async_request(self, request):
            callbacks.append(request)
            assert request.url.host == "backend.test"
            assert not {"cookie", "authorization", "x-avernet-principal", HEADER.lower()} & set(request.headers)
            return await super().handle_async_request(request)

    async def no_app_grant(_bot):
        raise AssertionError("built-in data must never look up an app delegation")

    async with AsyncExitStack() as stack:
        stack.callback(reset_principal_verifier_config_cache)
        platform = await build_local_platform(settings)
        if world is not None:
            stack.push_async_callback(platform.close)
            http = await stack.enter_async_context(httpx.AsyncClient(
                transport=CallbackTransport(backend), headers={"X-Avernet-Principal": "must-not-forward"},
                auth=("must-not-forward", "must-not-forward"),
            ))
            bridge = BotHttp(http, no_app_grant, data_origin="http://backend.test", data_key=KEY,
                             data_tenant=DEFAULT_AVERNET_TENANT)
            genome = HttpGenome(bridge)
            sessions = HttpSessionExperience(bridge, tmp_path / "episodes.db", stage="draft")
            stack.callback(sessions.close)

            class Governance(LocalGovernance):
                async def policy(self, bot):
                    policy = (await genome.resolve_revision(bot, "active")).policy
                    return BotPolicy(tuple(policy.mutable_genes), tuple(policy.pins))

            platform = Platform(
                None, genome, Governance(), platform.kills, sessions, platform.registry, platform.strategies,
                verification=build_rule_verifier(genome),
            )
        runtime = await compose(settings, platform)
        stack.push_async_callback(runtime.close)
        service.include_router(build_control_router(
            runtime, backend_access=BackendAccess(key=receiver_key, tenant=receiver_tenant),
        ))
        client = await stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(backend),
            base_url="http://backend.test",
            headers={"X-Avernet-Principal": mint(user_id=CALLER, tenant=None)},
            params={"user_id": CALLER, "entity_id": RESOURCE},
        ))
        yield (client, runtime, calls, collaborators) if world is None else (
            client, runtime, calls, collaborators, bots, callbacks, backend,
        )


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


@pytest.mark.asyncio
async def test_builtin_rsi_reuses_live_bot_access_without_an_app_grant(tmp_path, monkeypatch):
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    commit = AsyncMock(wraps=world.registry.commit_patch)
    monkeypatch.setattr(world.registry, "commit_patch", commit)
    bot = BotRef(RESOURCE, BOT, OWNER, "openclaw", "personal", "staff")
    owner = Provenance(ProvenanceKind.USER, OWNER)
    payload = {"bindings": [BINDING]}
    submit = {"binding_id": BINDING["id"]}
    idem = {"Idempotency-Key": "builtin-run"}
    async with wired(tmp_path, world=world) as (client, runtime, calls, collabs, _, callbacks, backend):
        assert not any(path.startswith(DATA_PREFIX) for path in backend.openapi()["paths"])
        missing = await client.put(URL + "/bindings", json=payload, headers={"If-Match": '\"\"'})
        assert missing.status_code == 409, missing.text
        assert "no_genome_baseline" in missing.text
        assert runtime.store.get_bindings(OWNER, BOT) is None
        assert all(grant.operator_user_id == CALLER for _, grant in calls)
        assert (await client.get(f"{DATA_PREFIX}/{BOT}/genome/drift")).status_code == 403

        # Seed the disposable Backend fixture explicitly. The RSI path must never do this.
        captured = await world.registry.capture(bot, created_by=owner)
        parent = captured.revision.revision.id
        world.registry.move_ref(bot, RefUpdate("active", parent, None), actor=owner, mover=RefMover.PROMOTION)
        saved = await client.put(URL + "/bindings", json=payload, headers={"If-Match": '\"\"'})
        assert saved.status_code == 200, saved.text
        assert (await client.post(URL + "/runs", json=submit, headers=idem)).status_code == 404
        collabs.level = PermissionLevel.ADMIN
        queued = await client.post(URL + "/runs", json=submit, headers=idem)
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["data"]["run"]
        assert runtime.store.get_run(run_id).trigger.operator_user_id == CALLER

        original = callbacks[-1]
        control_grant = calls[-1][0].headers[HEADER]
        path = original.url.path
        proof = original.headers[DATA_HEADER]
        assert (await client.get(path, params={}, headers={DATA_HEADER: control_grant})).status_code == 403
        assert (await client.get(path, params={"altered": "1"}, headers={DATA_HEADER: proof})).status_code == 403
        assert (await client.get(path.replace(f"/{BOT}/", "/another-bot/"), params={},
                                 headers={DATA_HEADER: proof})).status_code == 403

    # A fresh process uses the persisted initiating user, not the owner or an app credential.
    async with wired(tmp_path, world=world) as (client, runtime, _, collabs, bots, callbacks, _):
        collabs.level = PermissionLevel.ADMIN
        run = runtime.store.get_run(run_id)
        worker = "test-worker"
        claim = await runtime.channel.claim(worker, runtime.channel.tokens.issue(worker), [STRATEGY])
        assert claim and claim["run_id"] == run_id and run.trigger.operator_user_id == CALLER
        job, fence = claim["job_id"], claim["fencing_token"]
        files = await runtime.channel.files(job, fence)
        assert files["persona/SOUL.md"]
        episodes = await runtime.channel.experience_sessions(job, fence, days=7)
        assert [episode["messages"][0]["content"] for episode in episodes] == ["fixture message"]
        patch = {
            "patch_schema": 1, "base": parent, "rationale": "verify candidate recording",
            "evidence": [f"episode:{episodes[0]['episode_id']}"],
            "ops": [{"op": "file.edit", "target": "persona/SOUL.md", "edits": [
                {"kind": "insert_after", "anchor": files["persona/SOUL.md"].decode().splitlines()[0],
                 "content": "\n" + "\n".join(RULE_VALIDATION_MARKERS) + "\n"},
            ]}],
        }
        job_app = build_job_app(runtime.channel)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(job_app), base_url="http://job.test") as worker_http:
            candidate_url = f"/jobs/{job}/candidates"
            args = {"patch": patch, "fencing_token": fence}
            collabs.level = PermissionLevel.MEMBER
            denied = await worker_http.post(candidate_url, json=args)
            assert denied.status_code == 403, denied.text
            collabs.level = PermissionLevel.ADMIN
            result = await worker_http.post(candidate_url, json=args)
            assert result.status_code == 200, result.text
            revision = world.registry.get_revision(bot, result.json()["revision"])
            assert commit.await_count == 1
            assert commit.await_args.kwargs["created_by"].checked_as == CALLER != OWNER
            assert revision.revision.created_by.actor == STRATEGY
            assert revision.revision.created_by.run_id == run_id
            assert world.registry.get_revision(bot, "active").revision.id == parent
            write = next(request for request in callbacks if request.method == "POST")
            spoofed = httpx.Request("POST", write.url, json={
                "status": "candidate", "patch": patch,
                "run": {"run_id": "another-run", "strategy": STRATEGY, "allowed_genes": ["persona"]},
            })
            scope = run_data_scope(run)
            assert scope is not None
            spoofed.headers[DATA_HEADER] = issue_data_proof(
                KEY, DEFAULT_AVERNET_TENANT, scope,
                fingerprint("POST", spoofed.url.path, "", spoofed.content, spoofed.headers),
            )
            assert (await client.send(spoofed)).status_code == 403
            assert commit.await_count == 1
            candidate_id = result.json()["candidate_id"]
            verdict = await runtime.verifier.settle(candidate_id, run_id=run_id)
            assert verdict.status.value == "accept", verdict.reasons
            kinds = [event["kind"] for event in runtime.store.events.for_run(run_id)]
            assert kinds.index("verdict.finalized") < kinds.index("gate.decided")
            assert world.registry.get_revision(bot, "active").revision.id == parent
            collabs.level = PermissionLevel.NONE
            file_args = {"fencing_token": fence}
            assert (await worker_http.post(f"/jobs/{job}/files", json=file_args)).status_code == 403
            collabs.level = PermissionLevel.ADMIN
            bots.current_owner = "new-owner"
            assert (await worker_http.post(f"/jobs/{job}/files", json=file_args)).status_code == 403


@pytest.mark.asyncio
async def test_baseline_initialize_answers_rsi_cold_start_without_an_apply(tmp_path):
    """冷启动：基因组侧的显式建基线打通 RSI，RSI 路径自身仍不播种。"""
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    payload = {"bindings": [BINDING]}
    genome = f"/openapi/v1/bots/{BOT}/genome/baseline:initialize"
    bot = BotRef(RESOURCE, BOT, OWNER, "openclaw", "personal", "staff")
    async with wired(tmp_path, world=world) as (client, runtime, _calls, collabs, *_):
        # 契约不变：RSI 路径自己不建基线。
        missing = await client.put(URL + "/bindings", json=payload, headers={"If-Match": '\"\"'})
        assert missing.status_code == 409 and "no_genome_baseline" in missing.text
        assert runtime.store.get_bindings(OWNER, BOT) is None

        # 建基线是记录操作（ADMIN），不是晋升（OWNER + 编辑锁）；member 不够。
        # 公开面对权限不足统一回 404，不暴露 Bot 存在性。
        collabs.level = PermissionLevel.MEMBER
        denied = await client.post(genome)
        assert denied.status_code == 404, denied.text

        collabs.level = PermissionLevel.ADMIN
        first = await client.post(genome)
        assert first.status_code == 201, first.text
        assert first.json()["data"]["created"] is True
        revision = first.json()["data"]["revision"]
        active = world.registry.get_revision(bot, "active")
        assert active.revision.id == revision
        assert active.revision.parent is None
        assert active.revision.status is RevisionStatus.PROMOTED
        assert active.revision.created_by.actor == CALLER

        # 幂等：已有基线返回 200，不再 capture，active 不动。
        again = await client.post(genome)
        assert again.status_code == 200, again.text
        assert again.json()["data"] == {"revision": revision, "created": False}

        saved = await client.put(URL + "/bindings", json=payload, headers={"If-Match": '\"\"'})
        assert saved.status_code == 200, saved.text
        assert world.registry.get_revision(bot, "active").revision.id == revision
