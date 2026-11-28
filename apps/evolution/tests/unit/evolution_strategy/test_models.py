"""The strategy port's value types accept the shapes 03-strategy.zh-CN.md §2 promises."""

from __future__ import annotations

import pytest
from avernet_genome.patch_models import GenomePatch
from pydantic import ValidationError

from avernet_evolution.core.evolution_strategy import (
    AggregateScores,
    Budget,
    Candidate,
    RunSummary,
    Verdict,
    VerdictStatus,
)


def _persona_patch(base: str = "sha256:" + "a" * 64, evidence: list[str] | None = None) -> GenomePatch:
    return GenomePatch.model_validate(
        {
            "patch_schema": 1,
            "base": base,
            "ops": [
                {
                    "op": "file.edit",
                    "target": "persona/SOUL.md",
                    "edits": [
                        {
                            "kind": "replace_section",
                            "heading": "## Core Truths",
                            "content": "quote what you read\n",
                        }
                    ],
                }
            ],
            "rationale": "the bot asserted counts it never read",
            "evidence": evidence if evidence is not None else ["episode:ep_91"],
        }
    )


def test_run_summary_requires_a_line() -> None:
    assert RunSummary(summary="2 rounds, 1 candidate", metrics={"rounds": 2.0}).metrics == {"rounds": 2.0}
    with pytest.raises(ValueError):
        RunSummary(summary="   ")


def test_run_summary_metrics_must_be_numbers() -> None:
    with pytest.raises(ValueError):
        RunSummary(summary="ok", metrics={"rounds": "two"})  # type: ignore[dict-item]


def test_candidate_pins_rationale_and_evidence_to_the_patch() -> None:
    patch = _persona_patch()
    candidate = Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
    assert candidate.evidence == ("episode:ep_91",)

    with pytest.raises(ValueError):
        Candidate(patch=patch, rationale="something else", evidence=tuple(patch.evidence))
    with pytest.raises(ValueError):
        Candidate(patch=patch, rationale=patch.rationale, evidence=())


def test_verdict_carries_only_aggregates() -> None:
    verdict = Verdict(
        candidate="sha256:" + "c" * 64,
        status=VerdictStatus.ACCEPT,
        revision="sha256:" + "7" * 64,
        aggregates={"validation": AggregateScores(cases=30, seeds=3, mean_delta=0.061, ci=(0.028, 0.094))},
        reasons=("validation_improved",),
    )
    assert verdict.aggregates["validation"].mean_delta == pytest.approx(0.061)
    assert verdict.status is VerdictStatus.ACCEPT


def test_aggregate_scores_reject_a_bad_interval() -> None:
    with pytest.raises(ValueError):
        AggregateScores(cases=1, seeds=1, mean_delta=0.0, ci=(0.5, 0.1))
    with pytest.raises(ValueError):
        AggregateScores(cases=-1, seeds=1, mean_delta=0.0, ci=(0.0, 0.1))


def test_budget_carries_frozen_limits_and_usage() -> None:
    budget = Budget(max_usd=20.0, max_wall_clock_s=7200, max_rollouts=40, max_tokens=100_000)
    assert budget.spent_usd == 0.0 and budget.rollouts == 0


def test_persona_add_needs_no_base_file() -> None:
    patch = GenomePatch.model_validate(
        {
            "patch_schema": 1,
            "base": "sha256:" + "a" * 64,
            "ops": [{"op": "persona.add", "type": "CLAUDE.md", "content": "# Soul\n"}],
            "rationale": "the bot has no identity file",
            "evidence": ["episode:ep_1"],
        }
    )
    assert patch.ops[0].op == "persona.add"


def test_patch_rejects_an_unknown_evidence_ref_shape() -> None:
    with pytest.raises(ValidationError):
        GenomePatch.model_validate(
            {
                "patch_schema": 1,
                "base": "sha256:" + "a" * 64,
                "ops": [{"op": "persona.add", "type": "SOUL.md", "content": "# Soul\n"}],
                "rationale": "x",
                "evidence": ["not-a-ref"],
            }
        )
