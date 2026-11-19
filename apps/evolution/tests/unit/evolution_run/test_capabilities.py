"""能力链路端到端（06 §9.1）：策略真实调用 experience / models / agents / candidates。

这些测试用真实 `InProcessHost` + `build_run_context`，只把「平台外部依赖」
（经验存储、模型路由、智能体执行器）换成可编程替身。它们证明 06 把上下文槽位
装成了可调用对象，而不是 `CapabilityNotGranted` / `NotImplementedError` 空壳。
"""

from __future__ import annotations

import pytest

from avernet_evolution.core.evolution_run import (
    Binding,
    Budget,
    InProcessHost,
    ManualTrigger,
    OperationKind,
    OperationStatus,
    RunStatus,
    TriggerKind,
    TriggerRecord,
    build_run_context,
)
from avernet_evolution.core.evolution_strategy import (
    Candidate,
    Completion,
    RunSummary,
)

from .conftest import BOT, registration

MANUAL = TriggerRecord(kind=TriggerKind.MANUAL, requested_by="user_5")

NEEDS = {
    "experience.sessions@1": {},
    "agents@1": {"definitions": {"acme-fixer": {"engine": "openclaw", "path": "agents/fixer"}}},
}


class CapabilityStrategy:
    """真实调用 experience → models → agents → operations → candidates 的策略。"""

    async def run(self, ctx):
        ctx.requires("experience.sessions@1")
        ctx.requires("models@1")
        ctx.requires("agents@1")
        ctx.requires("candidates@1")

        episodes = await ctx.experience.sessions(days=7, limit=10)
        await ctx.log.info("read experience", episodes=len(episodes))

        completion = await ctx.models.complete(
            messages=[{"role": "user", "content": "diagnose"}],
        )

        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/fix")
        op_id = await ctx.agents.start(
            "acme-fixer",
            workspace=ws,
            prompt=completion.text,
            idempotency_key=f"{ctx.run_id}/fix",
        )
        op = await ctx.operations.wait(op_id)
        assert op.status.value == "succeeded", op.status

        current = (await ws.read("persona/SOUL.md")).decode("utf-8")
        await ws.write("persona/SOUL.md", current + "- fix applied\n")
        patch = await ws.to_patch(rationale="fix", evidence=("episode:ep_1",))
        await ctx.candidates.submit(
            Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
        )
        return RunSummary(
            summary="completed",
            metrics={"episodes": float(len(episodes)), "candidate": 1.0},
        )


class FakeModels:
    def __init__(self) -> None:
        self.calls: list[list] = []

    async def complete(self, *, messages, model=None, max_tokens=4096):
        self.calls.append(messages)
        return Completion(text="wrong tone", model="test/model", cost_usd=0.01)


class FakeExperience:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def sessions(self, *, days, limit=500, revision=None):
        self.calls.append((days, limit, revision))
        return [object()]

    async def feedback(self, *, days, kinds=None, limit=500):
        return []


class FakeAgents:
    def __init__(self, operations) -> None:
        self.operations = operations
        self.calls: list[dict] = []

    async def start(
        self,
        definition,
        *,
        workspace,
        prompt,
        idempotency_key,
        timeout_s=1800,
        run_id,
    ):
        self.calls.append({"definition": definition, "prompt": prompt, "run_id": run_id})
        op_id = await self.operations.start(
            run_id,
            OperationKind.AGENT_SESSION,
            idempotency_key=idempotency_key,
            request={},
        )
        await self.operations.record_result(
            run_id,
            op_id,
            status=OperationStatus.SUCCEEDED,
            result={"exit_status": "completed", "transcript_artifact": "art_1"},
        )
        return op_id


async def _snapshot(revision, *, bot=None):
    return {"persona/SOUL.md": b"## Core Truths\n- be useful\n"}


def _binding(**overrides) -> Binding:
    values = {
        "id": "bind_01",
        "strategy": "acme/identity-hardening@1.0.0",
        "trigger": ManualTrigger(),
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "default@1",
        "budget": Budget(max_usd=5.0, max_wall_clock_s=3600),
        "params": {},
    }
    values.update(overrides)
    return Binding(**values)


def _host(services, strategies, *, models, experience, agents):
    def factory(run, **kwargs):
        return build_run_context(
            run,
            store=services.store,
            genome=services.genome,
            budget=services.budget,
            operations=services.operations,
            **kwargs,
        )

    return InProcessHost(
        strategies=strategies,
        store=services.store,
        jobs=services.jobs,
        operations=services.operations,
        budget=services.budget,
        genome=services.genome,
        ctx_factory=factory,
        models_provider=models,
        experience_provider=experience,
        agents_provider=agents,
        heartbeat_s=3600,
    )


@pytest.mark.asyncio
async def test_in_process_capabilities_are_real(services) -> None:
    services.registry.registration = registration(needs=NEEDS)
    services.genome.snapshot_files = _snapshot

    await services.bindings.put_bindings(BOT, [_binding()], expected_etag=None, actor="u")
    run_id = await services.runs.submit(BOT, "bind_01", idempotency_key="k", trigger=MANUAL)

    models = FakeModels()
    experience = FakeExperience()
    agents = FakeAgents(services.operations)
    host = _host(
        services,
        {"acme/identity-hardening@1.0.0": CapabilityStrategy},
        models=models,
        experience=experience,
        agents=agents,
    )

    assert await host.run_once("worker-1") is True
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.candidate_count == 1
    assert float(run.budget_used.usd) == pytest.approx(0.01)
    assert experience.calls and experience.calls[0][0] == 7
    assert models.calls and models.calls[0][0]["content"] == "diagnose"
    assert agents.calls and agents.calls[0]["definition"] == "acme-fixer"
    assert agents.calls[0]["run_id"] == run_id


@pytest.mark.asyncio
async def test_missing_provider_is_refused_explicitly(services) -> None:
    """已授予但没有 provider：必须是显式 `NotImplementedError`，不是 `AttributeError`。"""

    services.registry.registration = registration(needs=NEEDS)
    services.genome.snapshot_files = _snapshot

    await services.bindings.put_bindings(BOT, [_binding()], expected_etag=None, actor="u")
    await services.runs.submit(BOT, "bind_01", idempotency_key="k", trigger=MANUAL)

    host = _host(
        services,
        {"acme/identity-hardening@1.0.0": CapabilityStrategy},
        models=FakeModels(),
        experience=FakeExperience(),
        agents=None,
    )
    assert await host.run_once("worker-1") is True
    assert "agents provider" in (host.last_error or "")


@pytest.mark.asyncio
async def test_ungranted_capability_raises_capability_not_granted(services) -> None:
    """未在 needs 中声明：与 03 §4.3 一致，抛 `CapabilityNotGranted`。"""

    from avernet_evolution.core.evolution_strategy import CapabilityNotGranted

    services.genome.snapshot_files = _snapshot

    class NeedsAgents:
        async def run(self, ctx):
            ctx.requires("agents@1")
            return RunSummary(summary="unreachable", metrics={})

    await services.bindings.put_bindings(BOT, [_binding()], expected_etag=None, actor="u")
    await services.runs.submit(BOT, "bind_01", idempotency_key="k", trigger=MANUAL)

    host = _host(
        services,
        {"acme/identity-hardening@1.0.0": NeedsAgents},
        models=FakeModels(),
        experience=FakeExperience(),
        agents=FakeAgents(services.operations),
    )
    assert await host.run_once("worker-1") is True
    assert CapabilityNotGranted.__name__ in (host.last_error or "")
