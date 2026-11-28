"""01–06 integration: real Registry/repositories, DI, worker and durable ledger.

Bot state reading, governance/strategy registration and experience use fixtures.
One acceptance test uses a separate HTTP worker process. No verification, gate,
promotion, real Bot, model API or sandbox is involved.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import pytest_asyncio
from injector import Injector, InstanceProvider, Module

from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.core.bot_genome.models import RefMover, RefUpdate
from avernet_evolution.core.evolution_ledger import LocalExperimentLedger
from avernet_evolution.core.evolution_run import (
    BindingsService,
    BudgetService,
    EvolutionStore,
    JobChannel,
    JobTokenIssuer,
    JobUnauthorized,
    OperationService,
    RunService,
    RunStatus,
    TriggerRecord,
    Worker,
)
from avernet_evolution.core.evolution_run.ports import (
    GovernancePort,
    KillSwitchesPort,
    StrategyRegistryPort,
)
from avernet_evolution.core.evolution_strategy import Candidate, RunSummary
from agentclaw.community.di.evolution_acceptance.jobs_module import EvolutionJobsModule
from agentclaw.community.di.evolution_acceptance.runtime_module import (
    EvolutionRuntimeModule,
)
from tests.community.factories.evolution_runtime import (
    FakeGovernance,
    FakeKills,
    FakeRegistry,
    registration,
)
from tests.community.factories.evolution_runtime import BINDING, REF

from .example_blobs import BlobDir, SUPPORT_BOT, load_patch, spec_of
from .service_fixtures import BOT, OWNER, World
from .test_capture import FakeReader


class _ExternalModule(Module):
    def __init__(self, registry):
        self.registry = registry

    def configure(self, binder):
        binder.bind(GenomeRegistryProtocol, to=self.registry)
        binder.bind(
            StrategyRegistryPort,
            to=InstanceProvider(
                FakeRegistry(
                    registration(
                        needs={
                            "experience.sessions@1": {},
                            "experience.feedback@1": {},
                        }
                    )
                )
            ),
        )
        binder.bind(
            GovernancePort,
            to=InstanceProvider(FakeGovernance(mutable=("persona", "resources"))),
        )
        binder.bind(KillSwitchesPort, to=InstanceProvider(FakeKills()))


def _injector(world):
    return Injector(
        [
            EvolutionJobsModule(),
            EvolutionRuntimeModule(),
            _ExternalModule(world.registry),
        ]
    )


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLUTION_DB_PATH", str(tmp_path / "runs.db"))
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    captured = await world.registry.capture(BOT, created_by=OWNER)
    parent = captured.revision.revision.id
    # Only this disposable fixture seeds active; the workflow must never move it.
    world.registry.move_ref(
        BOT, RefUpdate("active", parent, None), actor=OWNER, mover=RefMover.PROMOTION
    )
    injector = _injector(world)
    binding = replace(BINDING, allowed_genes=["persona", "resources"])
    await injector.get(BindingsService).put_bindings(
        BOT, [binding], expected_etag=None, actor=BOT.owner_id
    )
    run_id = await injector.get(RunService).submit(
        BOT,
        binding.id,
        idempotency_key="integration",
        trigger=TriggerRecord(kind="manual"),
    )
    try:
        yield world, injector, run_id, parent
    finally:
        injector.get(EvolutionStore).conn.close()
        injector.get(LocalExperimentLedger).conn.close()


class _WorkspaceStrategy:
    async def run(self, ctx):
        workspace = await ctx.workspace.materialise(ctx.parent.id, key="proposal")
        current = await workspace.read("persona/SOUL.md")
        await workspace.write(
            "persona/SOUL.md", current + b"\nPrefer plain language.\n"
        )
        patch = await workspace.to_patch(
            rationale="clarify persona", evidence=("episode:1",)
        )
        candidate = Candidate(
            patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence)
        )
        first = await ctx.candidates.submit(candidate)
        assert await ctx.candidates.submit(candidate) == first
        return RunSummary(summary="candidate recorded", metrics={"candidates": 1.0})


@pytest.mark.asyncio
async def test_di_worker_records_real_candidate_without_07_09(setup):
    world, injector, run_id, parent = setup
    channel = injector.get(JobChannel)
    runs = injector.get(RunService)
    assert runs.genome is channel.genome
    assert runs.bindings is injector.get(BindingsService)
    assert runs.budget is channel.budget is injector.get(BudgetService)
    assert runs.ledger is channel.ledger is injector.get(LocalExperimentLedger)
    assert channel.operations is injector.get(OperationService)
    assert channel.verifier is channel.promotion is None
    worker = Worker(
        transport=channel,
        strategies={REF: _WorkspaceStrategy},
        worker_id="worker",
        heartbeat_s=3600,
    )
    assert await worker.run_once(injector.get(JobTokenIssuer).issue("worker"))
    run = await runs.get(BOT, run_id)
    assert run.status is RunStatus.COMPLETED, run.summary
    assert run.parent_revision == parent and run.candidate_count == 1
    (candidate,) = await runs.candidates(BOT, run_id)
    (listed,) = await channel.ledger.list(bot=BOT)
    entry = await channel.ledger.get(listed.entry_id)
    assert entry.candidate_revision == candidate.revision
    assert entry.parent_revision == parent and entry.run_id == run_id
    assert len(entry.events) == 1 and entry.events[0]["type"] == "submitted"
    assert candidate.verdict == "pending"  # deliberately no evaluation
    assert world.registry.get_revision(BOT, "active").revision.id == parent
    assert (
        world.registry.get_revision(BOT, f"candidate/{run_id}/1").revision.id
        == candidate.revision
    )
    assert world.registry.list_revisions(BOT).total == 2
    assert world.applier.starts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "registry_before",
        "registry_after",
        "revision_before",
        "revision_after",
        "ledger_before",
        "ledger_after",
        "complete_before",
        "complete_after",
    ],
)
async def test_interrupted_submission_resumes_after_reopen(setup, monkeypatch, failure):
    world, injector, run_id, parent = setup
    channel = injector.get(JobChannel)
    grant = await channel.claim(
        "worker", injector.get(JobTokenIssuer).issue("worker"), [REF]
    )
    patch = load_patch("00-mixed-r1-to-r2.json")
    # The full example covers persona + resource, not just a fake persona op.
    digest = patch.ops[1].content.digest
    world.registry.put_content(BOT, BlobDir().read(digest), provenance=OWNER)
    target, after = failure.split("_")
    if target == "registry":
        owner, method = world.registry, "commit_patch"
    elif target == "revision":
        owner, method = channel.store, "record_candidate_revision"
    elif target == "ledger":
        owner, method = channel.ledger, "record_submission"
    else:
        owner, method = channel.store, "complete_submission"
    original = getattr(owner, method)

    async def fail_async(*args, **kwargs):
        if after == "after":
            await original(*args, **kwargs)
        raise OSError("injected persistence/response failure")

    def fail_sync(*args, **kwargs):
        if after == "after":
            original(*args, **kwargs)
        raise OSError("injected persistence/response failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(
            owner, method, fail_async if target in ("registry", "ledger") else fail_sync
        )
        with pytest.raises(OSError, match="injected"):
            await channel.submit(
                grant["job_id"], grant["fencing_token"], patch.to_json()
            )
    await channel.fail(grant["job_id"], grant["fencing_token"], "transient")
    channel.store.conn.close()
    channel.ledger.conn.close()

    # Recreate the complete DI graph: no in-memory completion flag can help.
    reopened = _injector(world)
    channel = reopened.get(JobChannel)
    with pytest.raises(JobUnauthorized):
        await channel.submit(grant["job_id"], grant["fencing_token"], patch.to_json())
    grant = await channel.claim(
        "retry-worker", reopened.get(JobTokenIssuer).issue("retry-worker"), [REF]
    )
    assert grant["attempt"] == 2 and grant["run_id"] == run_id
    first = await channel.submit(
        grant["job_id"], grant["fencing_token"], patch.to_json()
    )
    second = await channel.submit(
        grant["job_id"], grant["fencing_token"], patch.to_json()
    )
    assert first == second
    await channel.complete(
        grant["job_id"], grant["fencing_token"], {"summary": "resumed"}
    )
    assert channel.store.get_run(run_id).status is RunStatus.COMPLETED
    (candidate,) = channel.store.candidates(run_id)
    assert candidate.revision and candidate.seq == 1
    assert channel.store.submission_complete(run_id, candidate.candidate_id)
    assert channel.store.get_run(run_id).candidate_count == 1
    (listed,) = await channel.ledger.list(bot=BOT)
    entry = await channel.ledger.get(listed.entry_id)
    assert entry.candidate_revision == candidate.revision and entry.iteration == 1
    assert len(entry.events) == 1
    assert world.registry.list_revisions(BOT).total == 2
    assert world.registry.get_revision(BOT, "active").revision.id == parent
    assert (
        world.registry.get_revision(BOT, f"candidate/{run_id}/1").revision.id
        == candidate.revision
    )
    assert len(world.registry.list_refs(BOT)) == 2
    channel.store.conn.close()
    channel.ledger.conn.close()


@pytest.mark.asyncio
async def test_di_capability_providers_share_the_run_budget(setup):
    from avernet_evolution.core.evolution_strategy import Completion
    from avernet_evolution.core.evolution_strategy.protocols import (
        Models,
    )

    world, injector, run_id, _ = setup

    class ModelsProvider:
        async def complete(self, *, messages, model=None, max_tokens=4096):
            assert messages == [{"role": "user", "content": "diagnose"}]
            return Completion(text="clarify tone", model="test/model", cost_usd=0.25)

    class ExperienceProvider:
        async def sessions(self, *, days, limit=500, revision=None):
            return [{"episode_id": "episode:1", "days": days}]

        async def feedback(self, *, days, kinds=None, limit=500):
            return []

    from avernet_evolution.core.evolution_run.ports import ExperienceQueryFactory

    class Factory:
        def __init__(self):
            self.calls = []

        def for_bot(self, bot):
            self.calls.append(bot)
            return experience

    models, experience = ModelsProvider(), ExperienceProvider()
    factory = Factory()
    injector.binder.bind(Models, to=InstanceProvider(models))
    injector.binder.bind(ExperienceQueryFactory, to=InstanceProvider(factory))
    channel = injector.get(JobChannel)
    assert channel.models is models and channel.experience_factory is factory
    grant = await channel.claim(
        "worker", injector.get(JobTokenIssuer).issue("worker"), [REF]
    )
    args = grant["job_id"], grant["fencing_token"]
    assert await channel.experience_sessions(*args, days=7) == [
        {"episode_id": "episode:1", "days": 7}
    ]
    assert await channel.experience_feedback(*args, days=7) == []
    assert factory.calls == [BOT, BOT]
    completion = await channel.models_complete(
        *args, messages=[{"role": "user", "content": "diagnose"}]
    )
    assert completion["cost_usd"] == 0.25
    run = await injector.get(RunService).get(BOT, run_id)
    assert float(run.budget_used.usd) == 0.25
    assert world.applier.starts == []


@pytest.mark.asyncio
async def test_http_subprocess_smoke_records_pending_candidate(
    setup, tmp_path, monkeypatch
):
    from agentclaw.community.di.evolution_smoke import run_smoke

    import asyncio

    monkeypatch.setenv("SMOKE_AMBIENT_SECRET", "must-not-reach-worker")
    spawn = asyncio.create_subprocess_exec
    environments = []

    async def record_spawn(*args, **kwargs):
        environments.append(kwargs["env"])
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    world, _, _, _ = setup
    report = await run_smoke(
        registry=world.registry, bot=BOT, workdir=tmp_path / "smoke"
    )
    assert len(environments) == 1
    assert "SMOKE_AMBIENT_SECRET" not in environments[0]
    assert "DEPLOY_PROFILE" not in environments[0]
    assert report["status"] == "completed"
    assert report["verdict"] == "pending"
    assert report["conformance"] == "pending"
    assert report["protected_refs_unchanged"] and report["bot_config_unchanged"]
    assert (tmp_path / "smoke" / "report.json").is_file()
    assert world.applier.starts == []


@pytest.mark.asyncio
async def test_smoke_requires_baseline_and_fresh_workdir(tmp_path):
    from agentclaw.community.di.evolution_smoke import run_smoke
    from agentclaw.community.core.bot_genome.errors import RevisionNotFound

    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    with pytest.raises(RevisionNotFound):
        await run_smoke(registry=world.registry, bot=BOT, workdir=tmp_path / "missing")
    assert not (tmp_path / "missing").exists()
    assert world.registry.list_revisions(BOT).total == 0

    captured = await world.registry.capture(BOT, created_by=OWNER)
    world.registry.move_ref(
        BOT,
        RefUpdate("active", captured.revision.revision.id, None),
        actor=OWNER,
        mover=RefMover.PROMOTION,
    )
    with pytest.raises(FileExistsError):
        await run_smoke(registry=world.registry, bot=BOT, workdir=tmp_path)
    assert world.registry.list_revisions(BOT).total == 1
    assert world.applier.starts == []


@pytest.mark.asyncio
async def test_http_smoke_uses_ingested_episode_in_persisted_ledger(setup, tmp_path):
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from agentclaw.community.api.engine_runtime_service import BotFacts, EngineResult
    from agentclaw.community.core.engine_runtime.session_key import (
        PassThroughSessionKeyCodec,
        SessionKeyCodecRegistry,
    )
    from agentclaw.community.di.evolution_smoke import run_smoke
    from agentclaw.community.plugins.evolution.session_experience import (
        SessionSnapshotExperience,
    )

    world, _, _, _ = setup
    provider = SessionSnapshotExperience(
        bot=BOT,
        database=tmp_path / "experience.db",
        include_draft=True,
    )
    relay = AsyncMock()
    relay.resolve_bot_off_loop.return_value = BotFacts(
        BOT.bot_id,
        BOT.bot_type,
        BOT.engine_type,
        BOT.owner_id,
    )
    now = datetime.now(timezone.utc).isoformat()
    relay.call.side_effect = [
        EngineResult(
            {
                "id": "session:acceptance:user:owner",
                "gmt_created": now,
                "gmt_modified": now,
            }
        ),
        EngineResult(
            [{"role": "user", "content": "Please cite evidence, not guesses."}]
        ),
    ]
    try:
        episode = await provider.ingest(
            relay=relay,
            codecs=SessionKeyCodecRegistry(PassThroughSessionKeyCodec()),
            session_id="session:acceptance:user:owner",
            runtime_stage="draft",
        )
        report = await run_smoke(
            registry=world.registry,
            bot=BOT,
            workdir=tmp_path / "live-smoke",
            experience_factory=provider,
            expected_episode_ids=(episode,),
        )
        assert report["evidence"] == [f"episode:{episode}"]
        assert report["status"] == "completed" and report["verdict"] == "pending"
        assert provider.reads == 1 and relay.call.await_count == 2
        assert world.applier.starts == []
    finally:
        provider.close()


@pytest.mark.asyncio
async def test_smoke_drift_fails_before_worker_and_records_cancellation(setup, tmp_path):
    import sqlite3
    from agentclaw.community.di.evolution_smoke import run_smoke
    world, _, _, _ = setup
    protected = world.registry.list_refs(BOT)
    world.reader._persona["SOUL.md"] += "\nExternal change.\n"
    output = tmp_path / "drift-smoke"
    with pytest.raises(RuntimeError, match="was not queued: status=cancelled"):
        await run_smoke(registry=world.registry, bot=BOT, workdir=output)
    conn = sqlite3.connect(output / "runs.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM evolution_job").fetchone()[0] == 0
        kinds = [r[0] for r in conn.execute("SELECT kind FROM evolution_event")]
        assert kinds == ["run.cancelled"]
        assert (output / "run-id.txt").is_file()
        assert not (output / "report.json").exists()
        assert world.registry.list_refs(BOT) == protected
    finally:
        conn.close()
