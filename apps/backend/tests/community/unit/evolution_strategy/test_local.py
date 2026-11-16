"""The in-process strategy platform (03-strategy.zh-CN.md §8.4) honours the contract."""

from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.evolution_strategy import (
    Budget,
    BudgetExhausted,
    CapabilityNotGranted,
    Candidate,
    LocalContext,
    RunSummary,
    Verdict,
    VerdictStatus,
    build_context,
    candidate_id_of,
    run_once,
)
from agentclaw.community.core.evolution_strategy.local import StaticRevision


def run(coro):
    return asyncio.run(coro)


def _parent(files: dict[str, str] | None = None) -> StaticRevision:
    return StaticRevision(
        id="sha256:" + "a" * 64,
        seq="r1",
        files=dict(files or {}),
        spec={"persona": [{"type": "SOUL.md", "digest": "sha256:" + "b" * 64}]},
    )


def _candidate(parent: StaticRevision, text: str = "replacement") -> Candidate:
    ctx = build_context(parent=parent)
    ws = run(ctx.workspace.materialise(parent.id, key="ws-1"))
    run(ws.write("persona/SOUL.md", text))
    patch = run(ws.to_patch(rationale="harden identity", evidence=("episode:ep_91",)))
    return Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))


def test_materialise_is_idempotent_per_key() -> None:
    parent = _parent({"persona/SOUL.md": "original\n"})
    ctx = build_context(parent=parent)
    ws1 = run(ctx.workspace.materialise(parent.id, key="k1"))
    ws2 = run(ctx.workspace.materialise(parent.id, key="k1"))
    assert ws1.id == ws2.id
    run(ws1.write("persona/SOUL.md", "edited\n"))
    assert run(ws2.read("persona/SOUL.md")) == b"edited\n"


def test_workspace_edits_do_not_touch_the_parent() -> None:
    parent = _parent({"persona/SOUL.md": "original\n"})
    ctx = build_context(parent=parent)
    ws = run(ctx.workspace.materialise(parent.id, key="k2"))
    run(ws.write("persona/SOUL.md", "edited\n"))
    assert run(parent.read("persona/SOUL.md")) == b"original\n"


def test_to_patch_turns_edits_into_a_genome_patch() -> None:
    parent = _parent({"persona/SOUL.md": "original\n"})
    ctx = build_context(parent=parent)
    ws = run(ctx.workspace.materialise(parent.id, key="k3"))
    run(ws.write("persona/SOUL.md", "replacement\n"))
    patch = run(ws.to_patch(rationale="harden", evidence=("episode:ep_91",)))
    assert len(patch.ops) == 1
    assert patch.ops[0].op == "file.edit"
    assert patch.base == parent.id
    assert patch.rationale == "harden"
    assert patch.evidence == ["episode:ep_91"]


def test_to_patch_adds_a_missing_persona_file() -> None:
    parent = _parent({})
    ctx = build_context(parent=parent)
    ws = run(ctx.workspace.materialise(parent.id, key="k4"))
    run(ws.write("persona/CLAUDE.md", "# Soul\n"))
    patch = run(ws.to_patch(rationale="no identity file yet", evidence=("episode:ep_2",)))
    assert patch.ops[0].op == "persona.add"
    assert patch.ops[0].type == "CLAUDE.md"


def test_to_patch_refuses_an_unchanged_workspace() -> None:
    parent = _parent({"persona/SOUL.md": "original\n"})
    ctx = build_context(parent=parent)
    ws = run(ctx.workspace.materialise(parent.id, key="k5"))
    with pytest.raises(ValueError):
        run(ws.to_patch(rationale="nothing changed"))


def test_candidate_id_is_stable_and_content_addressed() -> None:
    parent = _parent({"persona/SOUL.md": "original\n"})
    first = candidate_id_of(_candidate(parent, text="A"))
    same = candidate_id_of(_candidate(parent, text="A"))
    other = candidate_id_of(_candidate(parent, text="B"))
    assert first == same
    assert first != other
    assert first.startswith("sha256:")


def test_verdict_starts_pending_and_can_be_decided() -> None:
    parent = _parent({"persona/SOUL.md": "original\n"})
    ctx = build_context(parent=parent)
    cid = run(ctx.candidates.submit(_candidate(parent)))
    assert run(ctx.candidates.verdict(cid)).status is VerdictStatus.PENDING

    ctx.candidates.decide(cid, Verdict(candidate=cid, status=VerdictStatus.ACCEPT,
                                       revision="sha256:" + "7" * 64))
    decided = run(ctx.candidates.verdict(cid))
    assert decided.status is VerdictStatus.ACCEPT
    assert decided.revision == "sha256:" + "7" * 64


def test_decide_rejects_a_mismatched_candidate_id() -> None:
    ctx = build_context(parent=_parent({}))
    with pytest.raises(ValueError):
        ctx.candidates.decide("sha256:" + "1" * 64,
                              Verdict(candidate="sha256:" + "2" * 64,
                                      status=VerdictStatus.ACCEPT))


def test_ungranted_capabilities_refuse() -> None:
    ctx = build_context(parent=_parent({}), granted=[])
    with pytest.raises(CapabilityNotGranted):
        run(ctx.models.complete(messages=[{"role": "user", "content": "hi"}]))
    with pytest.raises(CapabilityNotGranted):
        run(ctx.experience.sessions(days=7))
    with pytest.raises(CapabilityNotGranted):
        run(ctx.agents.start("tune", workspace=None, prompt="", idempotency_key="k"))
    with pytest.raises(CapabilityNotGranted):
        run(ctx.evaluation.start_train(None, idempotency_key="k"))


def test_requires_names_the_missing_capability() -> None:
    ctx = build_context(parent=_parent({}), granted=["models@1"])
    with pytest.raises(CapabilityNotGranted) as excinfo:
        ctx.requires("agents@1")
    assert "agents@1" in str(excinfo.value)


def test_budget_charge_raises_over_limit() -> None:
    ctx = build_context(parent=_parent({}), budget=Budget(max_usd=1.0, max_wall_clock_s=60))
    run(ctx.budget.charge(0.5, "round 1"))
    with pytest.raises(BudgetExhausted):
        run(ctx.budget.charge(0.6, "round 2"))
    assert ctx.budget.remaining().spent_usd == pytest.approx(0.5)


def test_budget_remaining_keeps_the_frozen_limits() -> None:
    ctx = build_context(parent=_parent({}), budget=Budget(max_usd=20.0, max_wall_clock_s=7200,
                                                         max_rollouts=40))
    remaining = ctx.budget.remaining()
    assert remaining.max_usd == 20.0
    assert remaining.max_rollouts == 40


def test_cancelled_token_flips() -> None:
    ctx = build_context(parent=_parent({}))
    assert ctx.cancelled.is_set() is False
    ctx.cancelled.cancel()
    assert ctx.cancelled.is_set() is True


def test_log_and_artifacts_collect() -> None:
    ctx = build_context(parent=_parent({}))
    run(ctx.log.info("diagnosed", findings=3))
    artifact_id = run(ctx.artifacts.put("diagnosis.md", b"# findings\n"))
    assert ctx.log.entries[0][1] == "diagnosed"
    assert ctx.artifacts.blobs[artifact_id] == b"# findings\n"


def test_workspace_read_missing_file_raises() -> None:
    parent = _parent({})
    ctx = build_context(parent=parent)
    ws = run(ctx.workspace.materialise(parent.id, key="k6"))
    with pytest.raises(FileNotFoundError):
        run(ws.read("persona/SOUL.md"))


def test_run_once_returns_the_strategy_summary() -> None:
    class _NoopStrategy:
        async def run(self, ctx: LocalContext) -> RunSummary:
            return RunSummary(summary="did nothing", metrics={"rounds": 0.0})

    summary = run_once(_NoopStrategy(), build_context(parent=_parent({})))
    assert summary.summary == "did nothing"
    assert summary.metrics == {"rounds": 0.0}


def test_run_once_rejects_a_wrong_return_type() -> None:
    class _BadStrategy:
        async def run(self, ctx: LocalContext) -> RunSummary:
            return "not a summary"  # type: ignore[return-value]

    with pytest.raises(TypeError):
        run_once(_BadStrategy(), build_context(parent=_parent({})))


def test_context_exposes_params_attempt_and_grants() -> None:
    ctx = build_context(parent=_parent({}), run_id="run_7f3", attempt=3,
                        params={"window_days": 7}, granted=["models@1"])
    assert ctx.run_id == "run_7f3"
    assert ctx.attempt == 3
    assert ctx.params["window_days"] == 7
    assert ctx.granted == ["models@1"]
