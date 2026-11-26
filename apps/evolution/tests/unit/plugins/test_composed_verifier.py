"""Category composition uses independent fixed cases, not strategy-selected tests."""

from dataclasses import replace

import pytest
from test_rule_verifier import BOT, CANDIDATE, PARENT, _Genome, _ref

from avernet_evolution.core.evolution_verify import (
    Case,
    CaseOrigin,
    CaseOriginKind,
    ExecutorKind,
    Grade,
    JudgingSpec,
    RolloutRecord,
    Split,
    VerdictStatus,
)
from avernet_evolution.plugins.verifiers.composed import (
    VerificationMethod,
    build_composed_verifier,
    markdown_secret_scan,
)


def quality(genome, gene, *, fail=False, error=False):
    cases = tuple(
        Case(
            case_id=f"{gene}/{split.value}",
            suite_id=f"fixture/{gene}",
            suite_version=1,
            split=split,
            digest="sha256:" + "a" * 64,
            origin=CaseOrigin(kind=CaseOriginKind.AUTHORED),
            name="fixture structural check, not real behavioral quality",
            grading=JudgingSpec(grader="fixture@1"),
        )
        for split in (Split.VALIDATION, Split.REGRESSION)
    )

    async def execute(bot, revision, case, seed):
        if error:
            raise RuntimeError("quality provider unavailable")
        assert bot == BOT
        text = b"".join(
            value for path, value in genome.files[revision].items() if path.startswith(gene + "/")
        )
        score = (
            (b"improved" in text) if case.split is Split.VALIDATION else not (fail and revision == CANDIDATE)
        )
        return RolloutRecord(
            case.case_id, revision, seed, ExecutorKind.LOCAL_SANDBOX, transcript_ref=str(int(score))
        )

    async def grade(case, rollout):
        return Grade(score=float(rollout.transcript_ref))

    return VerificationMethod(gene, "fixture-quality@1", cases, execute, grade)


def setup(genes, *, fail=False, error=False, inject_sensitive_marker=False):
    paths = {"persona": "persona/SOUL.md", "skills": "skills/arbitrary/SKILL.md"}
    before = {p: b"existing guidance" for p in paths.values()}
    after = {
        p: value + (b" improved" if gene in genes else b"")
        for gene, p in paths.items()
        for value in [before[p]]
    }
    if inject_sensitive_marker:
        after[paths[genes[0]]] += b" BEGIN PRIVATE KEY"
    genome = _Genome({PARENT: before, CANDIDATE: after})
    methods = [
        method
        for gene in paths
        for method in (
            quality(genome, gene, fail=fail, error=error),
            markdown_secret_scan(genome, gene),
        )
    ]
    return genome, methods


@pytest.mark.asyncio
@pytest.mark.parametrize("genes", [("persona",), ("skills",), ("persona", "skills")])
async def test_only_changed_categories_are_composed_with_paired_checks(genes):
    genome, methods = setup(genes)
    service = build_composed_verifier(genome, methods=methods)
    await service.verify(_ref())
    verdict = await service.settle(CANDIDATE, run_id="run_1")
    assert verdict.status is VerdictStatus.ACCEPT, verdict.reasons
    assert verdict.splits[Split.VALIDATION].cases == len(genes)
    assert verdict.splits[Split.REGRESSION].mean_delta == 0
    assert verdict.splits[Split.SAFETY].cases == len(genes)
    assert await service.cases_for(BOT, [Split.TRAIN]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "provider", "regression", "safety"])
async def test_missing_coverage_errors_or_new_failures_never_accept(failure):
    genome, methods = setup(
        ("skills",),
        fail=failure == "regression",
        error=failure == "provider",
        inject_sensitive_marker=failure == "safety",
    )
    if failure == "missing":
        methods = [m for m in methods if m.gene != "skills" or "scan" in m.name]
    service = build_composed_verifier(genome, methods=methods)
    await service.verify(_ref())
    verdict = await service.settle(CANDIDATE, run_id="run_1")
    assert verdict.status is (
        VerdictStatus.INCONCLUSIVE if failure in ("missing", "provider") else VerdictStatus.REJECT
    )


def test_duplicate_cases_unversioned_methods_and_holdout_fail_closed():
    genome, methods = setup(("persona",))
    with pytest.raises(ValueError, match="duplicate"):
        build_composed_verifier(genome, methods=[methods[0], methods[0]])
    with pytest.raises(ValueError, match="versioned"):
        build_composed_verifier(genome, methods=[replace(methods[0], name="not-versioned")])
    with pytest.raises(ValueError, match="holdout"):
        build_composed_verifier(
            genome, methods=[replace(methods[0], cases=(replace(methods[0].cases[0], split=Split.HOLDOUT),))]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("split", [Split.REGRESSION, Split.SAFETY])
async def test_each_method_retains_its_own_must_pass_threshold(split):
    genome, methods = setup(("persona", "skills"))
    skill = methods[2]
    original_execute = skill.execute

    async def execute(bot, revision, case, seed):
        rollout = await original_execute(bot, revision, case, seed)
        if case.split is split:
            return replace(rollout, transcript_ref="0.8" if revision == CANDIDATE else "1.0")
        return rollout

    methods[0] = replace(
        methods[0],
        cases=tuple(
            replace(case, grading=replace(case.grading, pass_threshold=0.5)) for case in methods[0].cases
        ),
    )
    methods[2] = replace(
        skill,
        execute=execute,
        cases=tuple(
            replace(case, grading=replace(case.grading, pass_threshold=0.9))
            for case in (
                *skill.cases,
                replace(skill.cases[0], case_id="skills/safety", split=Split.SAFETY),
            )
        ),
    )
    service = build_composed_verifier(genome, methods=methods)
    await service.verify(_ref())
    verdict = await service.settle(CANDIDATE, run_id="run_1")
    assert verdict.status is VerdictStatus.REJECT
    assert verdict.splits[split].newly_failing == [f"skills/{split.value}"]
