"""Shared external runtime ports for isolated unit and Registry integration tests."""

from __future__ import annotations

from types import SimpleNamespace

from agentclaw.community.core.bot_genome.models import BotRef
from avernet_evolution.core.evolution_run import (
    Binding, ManualTrigger, Budget,
)
from avernet_evolution.core.evolution_strategy import (
    ConformanceState,
    ConformanceStatus,
    Runtime,
    RuntimeKind,
    StrategyRegistration,
)

BOT = BotRef(
    entity_id="ent_1",
    bot_id="bot_123",
    owner_id="user_5",
    engine_type="openclaw",
    bot_type="personal",
)


def registration(needs: dict | None = None) -> StrategyRegistration:
    return StrategyRegistration(
        id="acme/identity-hardening",
        version="1.0.0",
        runtime=Runtime(RuntimeKind.IN_PROCESS, package="acme.identity_hardening"),
        needs=needs if needs is not None else {"experience.sessions@1": {}},
    )


class FakeRegistry:
    def __init__(self, registration: StrategyRegistration) -> None:
        self.registration = registration
        self.definitions = ()
        self.conformance = ConformanceStatus(ConformanceState.PASSED, "1.0.0")
        self.enabled = True
        self.problems_by_engine: dict[str, list[str]] = {}

    async def get(self, sid: str, ver: str):
        from avernet_evolution.core.evolution_strategy import StrategyNotFound

        if (sid, ver) != (self.registration.id, self.registration.version):
            raise StrategyNotFound(f"{sid}@{ver}")
        return self

    async def check_engine(self, sid: str, ver: str, engine: str) -> list[str]:
        return self.problems_by_engine.get(str(engine), [])

    async def list(self, *, engine=None):
        return [self]


class FakeGenome:
    def __init__(self, revision_id: str = "sha256:" + "a" * 64) -> None:
        self.revision_id = revision_id
        self.drifted = False
        self.committed: list[tuple[str, str]] = []
        self.moved: list[tuple[str, str]] = []

    async def drift(self, bot):
        return object() if self.drifted else None

    async def resolve_revision(self, bot, ref):
        # GenomeRevision 的两个面：`revision.id` 给运行记录用，`spec` 给快照展开用
        return SimpleNamespace(
            revision=SimpleNamespace(id=self.revision_id),
            spec=SimpleNamespace(persona=[], skills=[], resources=[]),
        )

    async def read_content(self, bot, digest):
        return b""

    async def snapshot_files(self, revision, *, bot=None):
        return {}

    async def commit_patch(
        self, bot, patch, run_id, strategy_ref, allowed_genes, created_by
    ):
        self.committed.append((run_id, strategy_ref))
        return "sha256:" + "b" * 64

    async def move_ref(self, bot, ref, revision_id, actor):
        self.moved.append((ref, revision_id))
        self.revision_id = revision_id


class FakeGovernance:
    def __init__(
        self, *, engine: str = "openclaw", mutable=("persona", "skills", "memory")
    ):
        self._engine = engine
        self._mutable = mutable
        self.profile_issue: str | None = None
        self.bot_ceiling_budget = Budget(max_usd=100.0, max_wall_clock_s=86400)
        self.tenant_ceiling_budget = Budget(max_usd=100.0, max_wall_clock_s=86400)

    async def engine(self, bot):
        return self._engine

    async def policy(self, bot):
        return SimpleNamespace(mutable_genes=list(self._mutable), pins=[])

    async def bot_ceiling(self, bot):
        return self.bot_ceiling_budget

    async def tenant_ceiling(self, bot):
        return self.tenant_ceiling_budget

    async def profile_problems(self, profile):
        return [self.profile_issue] if self.profile_issue else []


class FakeKills:
    def __init__(self) -> None:
        self.disabled: set[str] = set()
        self.frozen = False
        self.paused_flag = False

    async def strategy_disabled(self, strategy, version):
        return f"{strategy}@{version}" in self.disabled

    async def bot_frozen(self, bot):
        return self.frozen

    async def paused(self):
        return self.paused_flag


REF = "acme/identity-hardening@1.0.0"




BINDING = Binding(
    id="bind_01",
    strategy=REF,
    trigger=ManualTrigger(),
    parent="active",
    allowed_genes=["persona"],
    verification_profile="default@1",
    budget=Budget(max_usd=5.0, max_wall_clock_s=600),
    params={},
)
