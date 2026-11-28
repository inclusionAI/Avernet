"""Platform-owned verification methods composed by the candidate's changed genes.

No strategy names, Skill names, or model-authored validation cases. A file safety
method alone is deliberately insufficient for acceptance: each affected gene
must have independently authored validation, regression and safety coverage.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from avernet_evolution.core.evolution_verify import (
    CandidateRef,
    Case,
    CaseCategory,
    CaseOrigin,
    CaseOriginKind,
    ExecutorKind,
    Grade,
    JudgingSpec,
    LocalVerificationService,
    RolloutRecord,
    SeedPolicy,
    Split,
    VerificationProfile,
)

Gene = Literal["persona", "skills"]


@dataclass(frozen=True)
class VerificationMethod:
    """Platform cases and executor/grader for one gene and one versioned method."""

    gene: Gene
    name: str
    cases: tuple[Case, ...]
    execute: Callable[[Any, str, Case, int], Awaitable[RolloutRecord]]
    grade: Callable[[Case, RolloutRecord], Awaitable[Grade]]


def build_composed_verifier(
    genome: Any,
    *,
    methods: Sequence[VerificationMethod],
    profile_name: str = "behavior-quality@1",
) -> LocalVerificationService:
    """Select all configured methods for each actually changed category.

    Missing coverage/provider errors yield inconclusive through the standard
    pipeline, never a fallback to an unrelated marker profile. Executors receive
    both parent and candidate via paired evaluation, and the existing policy
    retains zero tolerance for new safety/regression failures.
    """
    methods = tuple(methods)
    by_case: dict[str, VerificationMethod] = {}
    identities = set()
    for method in methods:
        if method.gene not in ("persona", "skills") or "@" not in method.name:
            raise ValueError("verification methods require a supported gene and a versioned name")
        identity = (method.gene, method.name)
        if identity in identities:
            raise ValueError("duplicate verification method")
        identities.add(identity)
        for case in method.cases:
            if case.case_id in by_case:
                raise ValueError("composed case ids must be globally unique")
            if case.split not in (Split.VALIDATION, Split.REGRESSION, Split.SAFETY):
                raise ValueError("composed verification excludes train and holdout cases")
            by_case[case.case_id] = method

    async def candidate_cases(candidate: CandidateRef, splits: Sequence[Split]) -> list[Case]:
        bot = candidate.bot
        if bot is None:
            from avernet_genome.models import BotRef

            bot = BotRef(candidate.bot_id, candidate.bot_id, candidate.bot_owner_id, "openclaw", "personal")
        parent = await genome.resolve_revision(bot, candidate.parent_revision)
        child = await genome.resolve_revision(bot, candidate.revision)
        before = await genome.snapshot_files(parent, bot=bot)
        after = await genome.snapshot_files(child, bot=bot)
        changed = {
            path.split("/", 1)[0]
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        }
        if not changed or changed - {"persona", "skills"}:
            raise ValueError("composed profile requires a persona/local-skill candidate")
        selected = []
        required = {Split.VALIDATION, Split.REGRESSION, Split.SAFETY}
        for gene in sorted(changed):
            cases = [case for method in methods if method.gene == gene for case in method.cases]
            if not required <= {case.split for case in cases}:
                raise ValueError(f"missing independent validation/regression/safety methods for {gene}")
            selected.extend(case for case in cases if case.split in splits)
        return selected

    async def cases_for(_bot: Any, _splits: Sequence[Split]) -> list[Case]:
        return []  # No strategy-authored/train or cross-profile fallback.

    async def execute(bot: Any, revision: str, case: Case, seed: int) -> RolloutRecord:
        return await by_case[case.case_id].execute(bot, revision, case, seed)

    async def grade(case: Case, rollout: RolloutRecord) -> Grade:
        return await by_case[case.case_id].grade(case, rollout)

    profile = VerificationProfile(
        name=profile_name,
        splits=(Split.VALIDATION, Split.REGRESSION, Split.SAFETY),
        seeds=SeedPolicy(default=1, max=1, escalate_within=0),
        regression_tolerance=0,
    )
    return LocalVerificationService(
        profiles={profile.name: profile},
        default_profile=profile.name,
        cases_for=cases_for,
        candidate_cases_for=candidate_cases,
        execute=execute,
        grade=grade,
    )


def markdown_secret_scan(genome: Any, gene: Gene) -> VerificationMethod:
    """Generic literal-secret safety check, NOT a behavioral quality evaluator.

    Reads a fresh permission-checked snapshot on every execution. No cached Bot
    contents survive permission revocation or restart. It only recognizes the
    explicit private-key and common token prefixes; deployments can add scanners.
    """
    method = "markdown-secret-scan@1"
    case = Case(
        case_id=f"{gene}/{method}",
        suite_id=f"platform/{gene}/markdown-safety",
        suite_version=1,
        split=Split.SAFETY,
        digest="sha256:" + hashlib.sha256(method.encode()).hexdigest(),
        origin=CaseOrigin(kind=CaseOriginKind.AUTHORED),
        name=f"{gene} literal-secret scan",
        category=CaseCategory.SAFETY,
        grading=JudgingSpec(grader=method, pass_threshold=0.5),
    )

    async def execute(bot: Any, revision_id: str, case: Case, seed: int) -> RolloutRecord:
        revision = await genome.resolve_revision(bot, revision_id)
        files = await genome.snapshot_files(revision, bot=bot)
        texts = [
            value.decode("utf-8")
            for path, value in files.items()
            if path.startswith(gene + "/") and path.lower().endswith(".md")
        ]
        leaked = any(marker in text for text in texts for marker in ("BEGIN PRIVATE KEY", "sk-"))
        return RolloutRecord(
            case.case_id,
            revision_id,
            seed,
            ExecutorKind.LOCAL_SANDBOX,
            transcript_ref="literal-secret:present" if leaked else "literal-secret:absent",
        )

    async def grade(_case: Case, rollout: RolloutRecord) -> Grade:
        if rollout.transcript_ref not in ("literal-secret:present", "literal-secret:absent"):
            raise ValueError("missing literal-secret observation")
        return Grade(score=float(rollout.transcript_ref == "literal-secret:absent"), critique=method)

    return VerificationMethod(gene, method, (case,), execute, grade)
