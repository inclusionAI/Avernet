"""The reference strategy hardens identity files through the port."""

from __future__ import annotations

import pytest

from avernet_evolution.core.evolution_strategy import (
    Candidate,
    LocalContext,
    RunSummary,
    Verdict,
    VerdictStatus,
    build_context,
    run_once,
)
from avernet_evolution.core.evolution_strategy.errors import CapabilityNotGranted
from avernet_evolution.core.evolution_strategy.local import StaticRevision
from avernet_evolution.core.evolution_strategy.strategies import IdentityHardening


class _FakeEpisode:
    def __init__(self, episode_id: str) -> None:
        self.episode_id = episode_id


class _FakeExperience:
    async def sessions(self, *, days: int, limit: int = 500, revision: str | None = None):
        return [_FakeEpisode("ep_91"), _FakeEpisode("ep_92")]

    async def feedback(self, *, days: int, kinds: list[str] | None = None, limit: int = 500):
        return []


class _FakeCandidates:
    def __init__(self) -> None:
        self.seen: list[Candidate] = []
        self._verdicts: dict[str, Verdict] = {}

    async def submit(self, candidate: Candidate) -> str:
        self.seen.append(candidate)
        return "sha256:" + "candidate" + "z" * 55

    async def verdict(self, candidate_id: str) -> Verdict:
        return self._verdicts.get(candidate_id, Verdict(candidate=candidate_id, status=VerdictStatus.PENDING))

    def accept(self, candidate_id: str) -> None:
        self._verdicts[candidate_id] = Verdict(
            candidate=candidate_id,
            status=VerdictStatus.ACCEPT,
            revision="sha256:" + "7" * 64,
        )


def _ctx(strategy: IdentityHardening) -> LocalContext:
    ctx = build_context(
        parent=StaticRevision(
            id="sha256:" + "a" * 64,
            seq="r1",
            files={"persona/SOUL.md": "## Core Truths\n- keep it small\n"},
        ),
        granted=["experience.sessions@1", "candidates@1"],
    )
    ctx.experience = _FakeExperience()
    ctx.candidates = _FakeCandidates()  # type: ignore[assignment]
    return ctx


def _strategy(**kwargs) -> IdentityHardening:
    """Fast polling so the suite does not wait the default 5 s per verdict poll."""
    return IdentityHardening(poll_s=0.001, **kwargs)


def test_strategy_returns_a_run_summary() -> None:
    strategy = _strategy()
    ctx = _ctx(strategy)
    summary = run_once(strategy, ctx)
    assert isinstance(summary, RunSummary)
    assert "candidate" in summary.summary
    assert summary.metrics["candidates_submitted"] == 1.0


def test_strategy_skips_when_already_hardened() -> None:
    ctx = _ctx(_strategy())
    ctx.parent.files["persona/SOUL.md"] = (
        "## Core Truths\n"
        "- **先读后改，不动猜测。** 读懂现有代码与测试再动手；一个模糊的编辑比不编辑更危险。\n"
        "- **引用证据，不凭空断言。** 提及数字、配置或行为时，说明来自哪个文件哪一行。\n"
        "- **最小改动，先测再提。** 一个会话只解决一个问题，改完必须本地跑通相关测试。\n"
        "- **答完即止。** 问题被回答后不要追加未经询问的自我介绍或环境说明。\n"
        "- **不假装能力。** 不会的工具或 API 不要编造调用；先查文档或承认不知道。\n"
    )
    summary = run_once(_strategy(), ctx)
    assert summary.metrics["rounds"] == 0.0
    assert "already carries" in summary.summary


def test_strategy_submits_a_candidate_with_evidence() -> None:
    ctx = _ctx(_strategy())
    strategy = _strategy()
    run_once(strategy, ctx)
    assert len(ctx.candidates.seen) == 1
    candidate = ctx.candidates.seen[0]
    ops = candidate.patch.ops
    assert any(o.op == "file.edit" and o.target == "persona/SOUL.md" for o in ops)
    assert candidate.evidence


def test_cancelled_before_proposing_returns_quickly() -> None:
    ctx = _ctx(_strategy())
    ctx.cancelled.cancel()
    summary = run_once(_strategy(), ctx)
    assert summary.metrics["rounds"] == 0.0


def test_strategy_needs_experience() -> None:
    ctx = build_context(parent=StaticRevision(id="sha256:" + "a" * 64, seq="r1"), granted=["candidates@1"])
    with pytest.raises(CapabilityNotGranted):
        run_once(_strategy(), ctx)
