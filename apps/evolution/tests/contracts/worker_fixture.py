"""Isolated worker-protocol graph: real runtime services, fixture external ports.

Seed synchronously through the real SQLite store on the async runner's thread.
The handler then runs on that same event loop; no cross-thread SQLite exception
or test-only relaxation of production storage is needed. Registration/binding
validation and the real Genome Registry are covered by runtime integration tests.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from avernet_genome.models import BotRef

from avernet_evolution.core.evolution_run import (
    BudgetService,
    EvolutionStore,
    JobChannel,
    JobService,
    JobTokenIssuer,
    OperationService,
    RunStatus,
    TriggerRecord,
)
from avernet_evolution.core.evolution_run.budget import BudgetUsage
from avernet_evolution.core.evolution_run.enums import OperationKind
from avernet_evolution.core.evolution_run.records import Job, Operation, Run
from avernet_evolution.core.evolution_run.store import utcnow
from avernet_evolution.core.evolution_strategy.models import Completion

BOT = BotRef(
    entity_id="endpoint-entity",
    bot_id="endpoint-bot",
    owner_id="endpoint-owner",
    engine_type="openclaw",
    bot_type="personal",
)
PARENT = "sha256:" + "a" * 64
REVISION = "sha256:" + "b" * 64
STRATEGY = "test/worker@1.0.0"
RUN_ID = "endpoint-run"
JOB_ID = "endpoint-job"
FENCE = "endpoint-fence"
CANDIDATE_ID = "endpoint-candidate"
OPERATION_ID = "endpoint-operation"
TOKENS = JobTokenIssuer(b"endpoint-only-signing-fixture")
GRANTED = [
    "candidates@1",
    "experience.sessions@1",
    "experience.feedback@1",
    "models@1",
    "agents@1",
]
PATCH = {
    "patch_schema": 1,
    "base": PARENT,
    "ops": [{"op": "persona.add", "type": "SOUL.md", "content": "Be precise.\n"}],
    "rationale": "clarify identity",
    "evidence": ["episode:endpoint-episode"],
}
FILES = {"persona/AGENTS.md": b"Follow the user's instructions.\n"}


class FixtureGenome:
    def __init__(self):
        self.commits = []

    async def resolve_revision(self, bot, ref):
        assert bot == BOT and ref == PARENT
        return SimpleNamespace(revision=SimpleNamespace(id=PARENT))

    async def snapshot_files(self, revision, *, bot):
        assert bot == BOT and revision.revision.id == PARENT
        return dict(FILES)

    async def commit_patch(self, bot, patch, run_id, strategy_ref, allowed_genes, created_by):
        self.commits.append((bot, patch, run_id, strategy_ref))
        return REVISION


class FixtureExperience:
    def __init__(self):
        self.calls = []

    async def sessions(self, **kwargs):
        self.calls.append(("sessions", kwargs))
        return [{"episode_id": "endpoint-episode"}]

    async def feedback(self, **kwargs):
        self.calls.append(("feedback", kwargs))
        return [{"kind": "correction", "episode_id": "endpoint-episode"}]


class FixtureExperienceFactory:
    def __init__(self):
        self.bots = []
        self.provider = FixtureExperience()

    def for_bot(self, bot):
        self.bots.append(bot)
        return self.provider


class FixtureModels:
    def __init__(self):
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return Completion(text="fixture answer", model="fixture-model", cost_usd=0.25)


class FixtureAgents:
    def __init__(self, operations):
        self.operations = operations
        self.calls = []

    async def start(self, *, run_id, idempotency_key, **request):
        self.calls.append(request)
        return await self.operations.start(
            run_id,
            OperationKind.AGENT_SESSION,
            idempotency_key=idempotency_key,
            request=request,
        )


def seed_channel(*, queued=False, granted=GRANTED):
    """Persist a valid queued/claimed run, then return its real service graph."""
    store = EvolutionStore()
    run = Run(
        id=RUN_ID,
        bot=BOT,
        binding_id="endpoint-binding",
        idempotency_key="endpoint-key",
        trigger=TriggerRecord(kind="manual"),
        strategy="test/worker",
        strategy_version="1.0.0",
        parent_ref="active",
        parent_revision=PARENT,
        allowed_genes=["persona"],
        verification_profile="next-stage@1",
        params={},
        budget_max={"max_usd": 1, "max_wall_clock_s": 120},
        granted=list(granted),
        agent_definitions={},
        status=RunStatus.QUEUED,
        attempt=1,
        max_attempts=2,
        budget_used=BudgetUsage(),
        candidate_count=0,
        created_at=utcnow(),
    )
    store.insert_run(run, BOT.owner_id, BOT.bot_id, "seed")
    store.insert_job(Job(id=JOB_ID, run_id=RUN_ID, attempt=1))
    if not queued:
        assert store.claim_job("endpoint-worker", [STRATEGY], lease_seconds=60, fencing_token=FENCE)
        store.update_run(replace(run, status=RunStatus.RUNNING, started_at=utcnow()))
    store.reserve_candidate(RUN_ID, CANDIDATE_ID)
    store.record_candidate_revision(RUN_ID, CANDIDATE_ID, REVISION)
    store.complete_submission(RUN_ID, CANDIDATE_ID)
    store.insert_operation(
        Operation(
            id=OPERATION_ID,
            run_id=RUN_ID,
            kind=OperationKind.AGENT_SESSION,
            idempotency_key="seed-operation",
            created_at=utcnow(),
        )
    )
    budget = BudgetService(store)
    operations = OperationService(store, budget=budget)
    channel = JobChannel(
        store=store,
        jobs=JobService(store),
        genome=FixtureGenome(),
        tokens=TOKENS,
        budget=budget,
        operations=operations,
        experience_factory=FixtureExperienceFactory(),
        models=FixtureModels(),
        agents=FixtureAgents(operations),
    )
    return channel
