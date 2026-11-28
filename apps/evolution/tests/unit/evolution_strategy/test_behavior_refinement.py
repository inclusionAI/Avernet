"""One strategy, arbitrary names and three binding scopes; no external model calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from avernet_genome.mutation_scope import FrozenMutationScope, mutation_problems

from avernet_evolution.core.evolution_strategy import Completion
from avernet_evolution.core.evolution_strategy.local import StaticRevision, build_context
from avernet_evolution.core.evolution_strategy.strategies import BehaviorRefinement

FILES = {
    "persona/SOUL.md": "# Keep facts\n",
    "skills/arbitrary-name/SKILL.md": "# Existing guidance\n",
    "skills/arbitrary-name/scripts/check.py": "print('unchanged')\n",
}


def context(targets, edits=None):
    edits = (
        edits
        if edits is not None
        else [
            {"path": p, "content": FILES[p] + "- Check evidence.\n"}
            for p in FILES
            if p.endswith(".md") and any(p.startswith(t) for t in targets)
        ]
    )
    return build_context(
        parent=StaticRevision("sha256:" + "a" * 64, "r1", FILES),
        mutation_scope=FrozenMutationScope(targets=tuple(targets)),
        experience=SimpleNamespace(sessions=AsyncMock(return_value=[{"episode_id": "e1", "messages": []}])),
        models=SimpleNamespace(
            complete=AsyncMock(
                return_value=Completion(
                    json.dumps({"rationale": "Evidence justifies a checklist", "edits": edits}),
                    "fixture",
                    0.01,
                )
            )
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "targets",
    [("persona/SOUL.md",), ("skills/arbitrary-name",), ("persona/SOUL.md", "skills/arbitrary-name")],
)
async def test_generic_strategy_obeys_frozen_scope(targets):
    ctx = context(targets)
    summary = await BehaviorRefinement().run(ctx)
    assert summary.metrics["candidates_submitted"] == 1
    [candidate] = ctx.candidates.submitted.values()
    assert not mutation_problems(candidate.patch, ctx.mutation_scope)
    assert candidate.evidence == ("episode:e1",)
    assert {op.op for op in candidate.patch.ops} <= {"file.edit", "skill.update"}
    assert all("scripts/check.py" not in str(op) for op in candidate.patch.ops)
    assert FILES["persona/SOUL.md"] == "# Keep facts\n"  # read-only parent unchanged


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edits",
    [
        [{"path": "skills/other/SKILL.md", "content": "escape"}],
        [{"path": "persona/SOUL.md", "content": "x"}] * 2,
        [{"path": "persona/SOUL.md", "content": " "}],
        [{"path": "skills/arbitrary-name/scripts/check.py", "content": "rewrite"}],
    ],
)
async def test_model_cannot_expand_or_corrupt_scope(edits):
    ctx = context(("persona/SOUL.md",), edits)
    with pytest.raises(ValueError):
        await BehaviorRefinement().run(ctx)
    assert not ctx.candidates.submitted


@pytest.mark.asyncio
async def test_missing_scope_evidence_and_cancellation_never_submit():
    ctx = context(("persona/SOUL.md",))
    ctx.mutation_scope = None
    with pytest.raises(ValueError, match="frozen mutation_scope"):
        await BehaviorRefinement().run(ctx)
    ctx.mutation_scope = FrozenMutationScope(targets=("persona/SOUL.md",))
    ctx.experience.sessions.return_value = []
    await BehaviorRefinement().run(ctx)
    ctx.models.complete.assert_not_called()
    ctx.cancelled.cancel()
    await BehaviorRefinement().run(ctx)
    assert not ctx.candidates.submitted


def saved_output(ctx):
    from avernet_evolution.core.evolution_strategy.output import StrategyOutput

    [content] = ctx.artifacts.blobs.values()
    return StrategyOutput.model_validate_json(content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edits,reason",
    [
        ([], "empty_edits"),
        ([{"path": "persona/SOUL.md", "content": FILES["persona/SOUL.md"]}], "identical_content"),
    ],
)
async def test_no_change_preserves_model_rationale(edits, reason):
    ctx = context(("persona/SOUL.md",), edits)
    result = await BehaviorRefinement().run(ctx)
    output = saved_output(ctx)
    assert output.outcome == "no_changes" and output.reason == reason
    assert output.rationale == "Evidence justifies a checklist"
    assert output.attempt == ctx.attempt and output.raw_output
    assert reason in result.summary
    assert not ctx.candidates.submitted


@pytest.mark.asyncio
async def test_proposal_saved_before_submission_failure_and_redacted_only_for_display():
    secret = "sk-" + "a" * 30
    ctx = context(("persona/SOUL.md",), [{"path": "persona/SOUL.md", "content": "# " + secret}])
    ctx.candidates.submit = AsyncMock(side_effect=RuntimeError("submission failed"))
    with pytest.raises(RuntimeError, match="submission failed"):
        await BehaviorRefinement().run(ctx)
    output = saved_output(ctx)
    assert output.outcome == "changes_proposed"
    assert output.edits[0].changed and secret not in output.model_dump_json()
    assert "[REDACTED]" in output.edits[0].content
    candidate = ctx.candidates.submit.call_args.args[0]
    assert candidate.rationale == "Evidence justifies a checklist"
    # Observability redaction must not rewrite the proposed patch/workspace.
    workspace = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/behavior-refinement")
    assert secret.encode() in await workspace.read("persona/SOUL.md")


@pytest.mark.asyncio
async def test_invalid_output_is_bounded_redacted_and_not_echoed_in_exception():
    ctx = context(("persona/SOUL.md",))
    secret = "sk-" + "b" * 30
    ctx.models.complete.return_value = Completion(secret + "x" * 70_000, "fixture", 0.01)
    with pytest.raises(ValueError, match="invalid model proposal") as caught:
        await BehaviorRefinement().run(ctx)
    output = saved_output(ctx)
    assert output.outcome == "invalid_proposal" and output.raw_output_truncated
    assert len(output.raw_output) <= 64_000 and secret not in output.raw_output
    assert secret not in str(caught.value) and not ctx.candidates.submitted


@pytest.mark.asyncio
async def test_artifact_write_failure_prevents_success_and_submission():
    ctx = context(("persona/SOUL.md",))
    ctx.artifacts.put = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        await BehaviorRefinement().run(ctx)
    assert not ctx.candidates.submitted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,reason",
    [
        ("files", "no_authorized_markdown"),
        ("evidence", "no_session_evidence"),
        ("cancelled", "cancelled_before_proposing"),
    ],
)
async def test_early_exit_records_explicit_reason_without_calling_model(case, reason):
    ctx = context(("skills/missing" if case == "files" else "persona/SOUL.md",))
    if case == "evidence":
        ctx.experience.sessions.return_value = []
    if case == "cancelled":
        ctx.cancelled.cancel()
    await BehaviorRefinement().run(ctx)
    assert saved_output(ctx).reason == reason
    ctx.models.complete.assert_not_called()
