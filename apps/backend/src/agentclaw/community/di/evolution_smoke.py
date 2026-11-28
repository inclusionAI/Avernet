"""Explicit, non-production 01–06 acceptance composition; never apply or promote.

The caller supplies a real Registry and an already-baselined disposable Bot.
Experience defaults to a Bot-scoped fixture; an explicit snapshot factory
can be supplied for live 02 acceptance. Neither is a production 02 provider. The HTTP worker is a separate local process, NOT a security sandbox.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import sys

from injector import Injector, InstanceProvider, Module
import uvicorn

from avernet_evolution.adapters.jobs import build_job_app
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from avernet_genome.models import (
    BotRef,
    Provenance,
    ProvenanceKind,
)
from avernet_evolution.core.evolution_ledger import LocalExperimentLedger
from avernet_evolution.core.evolution_run import (
    Binding,
    BindingsService,
    Budget,
    BudgetService,
    EvolutionStore,
    JobChannel,
    JobTokenIssuer,
    ManualTrigger,
    RunService,
    RunStatus,
    TriggerRecord,
)
from agentclaw.community.plugins.evolution.genome_registry import (
    RegistryGenomePort,
)
from avernet_evolution.core.evolution_run.ports import (
    BotPolicy,
    ExperienceQueryFactory,
    GovernancePort,
    KillSwitchesPort,
    StrategyRegistryPort,
)
from avernet_evolution.core.evolution_strategy import (
    Runtime,
    RuntimeKind,
    StrategyRegistration,
)
from avernet_evolution.core.evolution_strategy.strategies.identity_hardening import (
    IdentityHardening,
)
from agentclaw.community.di.evolution_smoke_sandbox import SmokeSandbox, sandbox_worker
from agentclaw.community.di.evolution_acceptance.jobs_module import EvolutionJobsModule
from agentclaw.community.di.evolution_acceptance.runtime_module import (
    EvolutionRuntimeModule,
)

from avernet_evolution.core.evolution_strategy.registry import (
    InMemoryStrategyRegistry,
)

STRATEGY = "acme/identity-hardening@1.0.0"


def smoke_strategy() -> IdentityHardening:
    """01–06 stops after one pending verdict; do not wait for a 07 verifier."""
    return IdentityHardening(max_verdict_polls=0)


class _FixtureExperience:
    async def sessions(self, *, days, limit=500, revision=None):
        return [{"episode_id": "smoke-fixture"}][:limit]

    async def feedback(self, *, days, kinds=None, limit=500):
        return []


class _SmokePorts:
    """Single-Bot, finite-budget fixture governance and experience."""

    def __init__(self, bot: BotRef, policy, experience_factory):
        self.bot, self.genome_policy = bot, policy
        self.experience_factory = experience_factory
        self.reads = 0

    def for_bot(self, bot):
        if bot != self.bot:
            raise PermissionError("smoke experience belongs to another Bot")
        self.reads += 1
        return (
            self.experience_factory.for_bot(bot)
            if self.experience_factory is not None
            else _FixtureExperience()
        )

    async def engine(self, bot):
        return bot.engine_type

    async def policy(self, bot):
        return BotPolicy(
            mutable_genes=tuple(self.genome_policy.mutable_genes),
            pins=tuple(self.genome_policy.pins),
        )

    async def bot_ceiling(self, bot):
        return Budget(max_usd=1, max_wall_clock_s=120)

    tenant_ceiling = bot_ceiling

    async def profile_problems(self, profile):
        return [] if profile == "smoke-no-evaluation@1" else ["unknown smoke profile"]

    async def strategy_disabled(self, strategy, version):
        return False

    async def bot_frozen(self, bot):
        return False

    async def paused(self):
        return False


class _SmokeModule(Module):
    def __init__(self, registry, strategy_registry, ports, database):
        self.registry, self.strategy_registry = registry, strategy_registry
        self.ports, self.database = ports, database

    def configure(self, binder):
        # InstanceProvider prevents Injector from interpreting Protocols as classes.
        for contract, instance in (
            (GenomeRegistryProtocol, self.registry),
            (StrategyRegistryPort, self.strategy_registry),
            (GovernancePort, self.ports),
            (KillSwitchesPort, self.ports),
            (ExperienceQueryFactory, self.ports),
            (EvolutionStore, EvolutionStore(self.database)),
            (LocalExperimentLedger, LocalExperimentLedger(f"{self.database}.ledger")),
        ):
            binder.bind(contract, to=InstanceProvider(instance))


async def _worker(channel: JobChannel, issuer: JobTokenIssuer, timeout_s: float) -> int:
    """Serve on the same loop as Registry (including in-memory SQLite fixtures)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(
                build_job_app(channel),
                log_level="error",
                access_log=False,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        process = None
        try:
            async with asyncio.timeout(timeout_s):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("smoke HTTP server stopped before startup")
                    await asyncio.sleep(0.01)
                # Do not pass the Backend's ambient credentials/config to the worker.
                env = {
                    "PATH": os.defpath,
                    "PYTHONPATH": os.pathsep.join(
                        str(Path(p).resolve()) for p in sys.path if p
                    ),
                    "PYTHONIOENCODING": "utf-8",
                    "EVOLUTION_JOB_ENDPOINT": endpoint,
                    "EVOLUTION_WORKER_ID": "smoke-worker",
                    "EVOLUTION_JOB_TOKEN": issuer.issue("smoke-worker"),
                    "EVOLUTION_WORKER_STRATEGIES": (
                        f"{STRATEGY}=agentclaw.community.di.evolution_smoke:smoke_strategy"
                    ),
                }
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "avernet_evolution.plugins.entrypoint",
                    env=env,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                return await process.wait()
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            server.should_exit = True
            await task


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def run_smoke(
    *,
    registry: GenomeRegistryProtocol,
    bot: BotRef,
    workdir: Path,
    timeout_s: float = 60,
    experience_factory: ExperienceQueryFactory | None = None,
    expected_episode_ids: tuple[str, ...] = (),
    sandbox_config: SmokeSandbox | None = None,
) -> dict:
    """Record a candidate using real Registry + shared DI + HTTP subprocess.

    Requires an existing active baseline; this tool NEVER creates/moves active.
    Creates a fresh run/ledger DB and writes report.json only after all checks.
    Registry capture/candidate records are retained; no real Bot config is applied.
    """
    if timeout_s <= 0 or timeout_s > 120:
        raise ValueError("timeout_s must be in (0, 120]")
    if experience_factory is not None and not expected_episode_ids:
        raise ValueError("live experience requires expected episode evidence")
    if experience_factory is None and expected_episode_ids:
        raise ValueError("episode evidence requires an explicit experience factory")
    expected_evidence = (
        tuple(f"episode:{e}" for e in expected_episode_ids)
        if experience_factory is not None
        else ("episode:smoke-fixture",)
    )
    active = registry.get_revision(bot, "active")  # Fail before writes if unbaselined.
    protected = [r for r in registry.list_refs(bot) if r.name in ("active", "previous")]
    workdir.mkdir(parents=True, exist_ok=False, mode=0o700)
    actor = Provenance(kind=ProvenanceKind.USER, actor=bot.owner_id)
    before = await registry.capture(bot, created_by=actor)
    ports = _SmokePorts(bot, active.policy, experience_factory)
    strategies = InMemoryStrategyRegistry()
    registered = await strategies.register(
        StrategyRegistration(
            id="acme/identity-hardening",
            version="1.0.0",
            runtime=(Runtime(RuntimeKind.JOB_WORKER, image=sandbox_config.image)
                     if sandbox_config is not None else
                     Runtime(RuntimeKind.IN_PROCESS, package="identity-hardening-smoke")),
            needs={"experience.sessions@1": {}},
        ),
        {},
    )
    injector = Injector(
        [
            EvolutionJobsModule(),
            EvolutionRuntimeModule(),
            _SmokeModule(registry, strategies, ports, workdir / "runs.db"),
        ]
    )
    store, ledger = injector.get(EvolutionStore), injector.get(LocalExperimentLedger)
    try:
        runs, bindings = injector.get(RunService), injector.get(BindingsService)
        channel = injector.get(JobChannel)
        _require(
            runs.genome is channel.genome is injector.get(RegistryGenomePort),
            "Registry adapter is not shared",
        )
        _require(
            runs.budget is channel.budget is injector.get(BudgetService),
            "run budget is not shared",
        )
        _require(
            runs.ledger is channel.ledger is ledger, "experiment ledger is not shared"
        )
        _require(
            channel.verifier is None and channel.promotion is None,
            "01–06 smoke must not install 07/08",
        )
        await bindings.put_bindings(
            bot,
            [
                Binding(
                    id="smoke",
                    strategy=STRATEGY,
                    trigger=ManualTrigger(),
                    parent="active",
                    allowed_genes=["persona"],
                    verification_profile="smoke-no-evaluation@1",
                    budget=Budget(max_usd=1, max_wall_clock_s=120),
                    auto_promote_ceiling=None,
                )
            ],
            expected_etag=None,
            actor=bot.owner_id,
        )
        run_id = await runs.submit(
            bot,
            "smoke",
            idempotency_key=secrets.token_hex(8),
            trigger=TriggerRecord(kind="manual"),
        )
        submitted = await runs.get(bot, run_id)
        (workdir / "run-id.txt").write_text(run_id + "\n")
        _require(submitted.status is RunStatus.QUEUED,
                 f"run {run_id} was not queued: status={submitted.status.value}, "
                 f"end_reason={submitted.end_reason}; active/config were not changed")
        for episode_id in expected_episode_ids:
            store.events.append("experience.snapshot.persisted", run_id=run_id,
                                details={"episode_ids": [episode_id], "count": 1})
        print(json.dumps({"event": "acceptance.run.queued", "run_id": run_id}), flush=True)
        if sandbox_config is None:
            exit_code = await _worker(channel, injector.get(JobTokenIssuer), timeout_s)
        else:
            exit_code = await sandbox_worker(
                channel=channel, issuer=injector.get(JobTokenIssuer),
                config=sandbox_config, workdir=workdir, timeout_s=timeout_s,
            )
        run = await runs.get(bot, run_id)
        _require(
            exit_code == 0 and run.status is RunStatus.COMPLETED,
            f"worker failed: exit={exit_code}, run={run.status.value}; inspect runs.db",
        )
        candidates = await runs.candidates(bot, run_id)
        _require(
            len(candidates) == 1 and run.candidate_count == 1,
            "expected one candidate; use a test Bot missing the reference core truths",
        )
        candidate = candidates[0]
        _require(
            candidate.verdict == "pending" and bool(candidate.revision),
            "candidate must remain pending with a persisted revision",
        )
        ref = f"candidate/{run_id}/1"
        _require(
            registry.get_revision(bot, ref).revision.id == candidate.revision,
            "candidate ref/revision mismatch",
        )
        entries = await ledger.list(bot=bot)
        _require(len(entries) == 1, "expected one ledger entry")
        entry = await ledger.get(entries[0].entry_id)
        _require(
            entry.candidate_revision == candidate.revision
            and entry.run_id == run_id
            and entry.evidence == expected_evidence
            and len(entry.events) == 1
            and entry.events[0]["type"] == "submitted",
            "ledger revision/evidence/submission mismatch",
        )
        _require(ports.reads == 1, "Bot-scoped experience provider was not invoked")
        after = await registry.capture(bot, created_by=actor)
        _require(
            before.revision.spec.to_json() == after.revision.spec.to_json(),
            "Bot configuration changed during smoke",
        )
        _require(
            protected
            == [r for r in registry.list_refs(bot) if r.name in ("active", "previous")],
            "protected refs changed during smoke",
        )
        report = {
            "scope": "01-06",
            "transport": ("HTTP + isolated single-use worker Pod" if sandbox_config is not None
                          else "HTTP + separate local process (not sandbox)"),
            "experience": (
                "explicit Bot-scoped provider (not session-export/v2)"
                if experience_factory is not None
                else "Bot-scoped fixture (not live 02 ingestion)"
            ),
            "evidence": list(entry.evidence),
            "conformance": registered.conformance.status.value,
            "run_id": run_id,
            "status": run.status.value,
            "parent_revision": run.parent_revision,
            "candidate_id": candidate.candidate_id,
            "candidate_revision": candidate.revision,
            "candidate_ref": ref,
            "verdict": candidate.verdict,
            "ledger_entries": len(entries),
            "protected_refs_unchanged": True,
            "bot_config_unchanged": True,
        }
        events = store.events.for_run(run_id)
        if sandbox_config is not None:
            kinds = {e["kind"] for e in events}
            _require({"sandbox.created", "job.claimed", "sandbox.exited", "sandbox.destroyed"}
                     <= kinds, "sandbox lifecycle evidence incomplete")
        (workdir / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events)
        )
        (workdir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        store.conn.close()
        ledger.conn.close()
