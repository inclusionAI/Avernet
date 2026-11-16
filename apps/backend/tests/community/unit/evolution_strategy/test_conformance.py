"""The conformance kit (§8.4) separates a conforming strategy from a broken one."""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_strategy import (
    Candidate,
    ConformanceState,
    RunSummary,
    Runtime,
    RuntimeKind,
    StaticRevision,
    StrategyRegistration,
    run_conformance,
)
from agentclaw.community.core.evolution_strategy.local import run_once
from agentclaw.community.core.evolution_strategy.strategies import IdentityHardening


def registration(needs: dict | None = None) -> StrategyRegistration:
    return StrategyRegistration(
        id="acme/identity-hardening",
        version="1.0.0",
        runtime=Runtime(RuntimeKind.IN_PROCESS, package="acme.identity_hardening"),
        needs=needs if needs is not None else {"experience.sessions@1": {}},
    )


def parent() -> StaticRevision:
    return StaticRevision(
        id="sha256:" + "a" * 64, seq="r1",
        files={"persona/SOUL.md": "## Core Truths\n- keep it small\n"},
    )


class _Experience:
    def __init__(self) -> None:
        class Episode:
            def __init__(self, episode_id: str) -> None:
                self.episode_id = episode_id

        self._episodes = [Episode("ep_1"), Episode("ep_2")]

    async def sessions(self, *, days: int, limit: int = 500, revision: str | None = None):
        return self._episodes

    async def feedback(self, *, days: int, kinds: list[str] | None = None, limit: int = 500):
        return []


@pytest.fixture(autouse=True)
def _provide_experience(monkeypatch):
    from agentclaw.community.core.evolution_strategy import local

    original = local.build_context

    def build(**kwargs):
        ctx = original(**kwargs)
        ctx.experience = _Experience()
        return ctx

    monkeypatch.setattr(local, "build_context", build)
    monkeypatch.setattr(
        "agentclaw.community.core.evolution_strategy.conformance.build_context", build
    )


def test_reference_strategy_passes_the_kit() -> None:
    report = run_conformance(
        lambda: IdentityHardening(poll_s=0.001),
        registration(),
        parent=parent(),
        mutable_genes=["persona/"],
    )
    assert report.status.status is ConformanceState.PASSED, report.failures()


class _UsesUndeclaredCapability:
    async def run(self, ctx):
        await ctx.agents.start("tune", workspace=None, prompt="", idempotency_key="k")
        return RunSummary(summary="used agents without declaring it")


def test_undeclared_capability_fails_the_kit() -> None:
    report = run_conformance(_UsesUndeclaredCapability, registration(), parent=parent())
    names = {check.name for check in report.failures()}
    assert "granted_capabilities_only" in names


class _IgnoresCancellation:
    async def run(self, ctx):
        import asyncio

        await asyncio.sleep(5)
        return RunSummary(summary="kept going")


def test_ignoring_cancellation_fails_the_kit() -> None:
    report = run_conformance(_IgnoresCancellation, registration(), parent=parent(), timeout_s=0.2)
    names = {check.name for check in report.failures()}
    assert "stops_on_cancel" in names


class _OutOfScopeEdit:
    async def run(self, ctx):
        ws = await ctx.workspace.materialise(ctx.parent.id, key="k")
        await ws.write("skills/other/SKILL.md", b"# x\n")
        patch = await ws.to_patch(rationale="edit a skill", evidence=("episode:ep_1",))
        await ctx.candidates.submit(
            Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
        )
        return RunSummary(summary="edited outside allowed genes")


def test_out_of_scope_candidate_fails_the_kit() -> None:
    report = run_conformance(_OutOfScopeEdit, registration(), parent=parent(),
                             mutable_genes=["persona/"])
    failures = {check.name for check in report.failures()}
    assert "candidate_validity" in failures


def test_skipped_check_reports_pending_not_passed() -> None:
    """A granted-but-unprovided capability makes a check skippable, not failing."""

    class _UsesUndeclaredAgents:
        async def run(self, ctx):
            await ctx.agents.start("tune", workspace=None, prompt="", idempotency_key="k")
            return RunSummary(summary="used agents")

    report = run_conformance(_UsesUndeclaredAgents, registration(needs={"agents@1": {}}),
                             parent=parent())
    assert report.status.status is ConformanceState.PENDING
    assert any(check.skipped for check in report.checks)


def test_strategy_can_be_run_once_outside_the_kit() -> None:
    from agentclaw.community.core.evolution_strategy import build_context

    ctx = build_context(parent=parent(), granted=["experience.sessions@1"])
    ctx.experience = _Experience()
    summary = run_once(IdentityHardening(poll_s=0.001), ctx)
    assert summary.metrics["candidates_submitted"] == 1.0
