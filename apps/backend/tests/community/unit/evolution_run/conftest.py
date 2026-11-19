"""Evolution runtime unit fixtures; reusable external ports live in factories."""

from types import SimpleNamespace

import pytest

from agentclaw.community.core.evolution_run import (
    BindingsService,
    BudgetService,
    EvolutionStore,
    JobService,
    OperationService,
    RunService,
)
from tests.community.factories.evolution_runtime import (  # noqa: F401 -- unit re-exports
    BOT,
    FakeGenome,
    FakeGovernance,
    FakeKills,
    FakeRegistry,
    registration,
)


@pytest.fixture
def services():
    store = EvolutionStore(":memory:")
    registry = FakeRegistry(registration())
    genome = FakeGenome()
    governance = FakeGovernance()
    kills = FakeKills()
    budget = BudgetService(store, governance=governance)
    bindings = BindingsService(
        store, registry=registry, genome=genome, governance=governance, kills=kills
    )
    runs = RunService(
        store,
        bindings=bindings,
        registry=registry,
        genome=genome,
        budget=budget,
        kills=kills,
    )
    jobs = JobService(store, lease_seconds=60, max_attempts=3)
    operations = OperationService(store, budget=budget)
    return SimpleNamespace(
        store=store,
        registry=registry,
        genome=genome,
        governance=governance,
        kills=kills,
        budget=budget,
        bindings=bindings,
        runs=runs,
        jobs=jobs,
        operations=operations,
    )
