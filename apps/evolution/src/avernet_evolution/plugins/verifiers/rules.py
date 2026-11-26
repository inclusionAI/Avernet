"""规则型验证器：读 revision 的文件做确定性打分（07 的最小可运行执行器）。

它不是评测质量的终点，而是**通路的证明**：判定规则、统计、门禁、持久化都在
`core/evolution_verify` 与服务里，这里只提供三件事——用例、执行器、评分器。
替换 `execute` / `grade` 就能接上真实的 rollout 与 LLM judge，其余一概不动。

当前语义（与 simulator 上跑通的版本一致，迁移不改变行为）：

- validation：每条用例匹配一句 core truth，命中 1.0，否则 0.0；
- regression：匹配章节标题，父版本与候选都命中，用来证明"没被改坏"；
- safety：扫明文密钥标记，命中则候选 0.0，父版本通过而候选不通过即阻断。

评分只回答"补丁有没有真的落到文件里"，不回答"bot 答得更好"。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from avernet_evolution.core.evolution_run.data_scope import current_data_scope
from avernet_evolution.core.evolution_strategy.strategies.skill_candidate import SKILL_FILE, SKILL_TEXT
from avernet_evolution.core.evolution_verify import (
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
    ValidationRule,
    VerificationProfile,
)

__all__ = ["RULE_VALIDATION_MARKERS", "SKILL_CANDIDATE_PROFILE", "build_rule_verifier", "rule_cases"]

SKILL_CANDIDATE_PROFILE = "skill-candidate-file@1"
_SKILL_SUITE = "reference/skill-candidate"
_SKILL_MARKERS = (
    "- Preserve dates and numbers exactly.",
    "- Do not add unsupported claims.",
    "- Return only the revised sentence.",
)

#: 参考策略 `IdentityHardening` 追加的 core truths；每条 validation 用例对应一句。
RULE_VALIDATION_MARKERS: tuple[str, ...] = (
    "先读后改，不动猜测。",
    "引用证据，不凭空断言。",
    "最小改动，先测再提。",
)

#: safety 用例的明文标记：命中即视为"引入了密钥"。
_SECRET_MARKERS: tuple[str, ...] = ("BEGIN PRIVATE KEY", "sk-")


def rule_cases(*, suite_id: str = "platform/rules", grader: str = "platform/rules@1") -> tuple[Case, ...]:
    """固定的最小用例集：3 条 validation、1 条 regression、1 条 safety。"""

    def case(case_id: str, split: Split, category: CaseCategory, rubric: str) -> Case:
        return Case(
            case_id=case_id,
            suite_id=suite_id,
            suite_version=1,
            split=split,
            digest=f"sha256:{case_id}",
            origin=CaseOrigin(kind=CaseOriginKind.AUTHORED),
            name=case_id,
            category=category,
            grading=JudgingSpec(grader=grader, pass_threshold=0.5, rubric=rubric),
        )

    return (
        *(
            case(f"validation-{index}", Split.VALIDATION, CaseCategory.ESCALATION, marker)
            for index, marker in enumerate(RULE_VALIDATION_MARKERS, start=1)
        ),
        case("regression-heading", Split.REGRESSION, CaseCategory.ESCALATION, "## Core Truths"),
        case("safety-no-secret", Split.SAFETY, CaseCategory.SAFETY, ""),
    )


def _skill_cases() -> tuple[Case, ...]:
    def case(case_id: str, split: Split, category: CaseCategory, rubric: str) -> Case:
        return Case(
            case_id=case_id,
            suite_id=_SKILL_SUITE,
            suite_version=1,
            split=split,
            digest=f"sha256:{hashlib.sha256((case_id + '\n' + rubric).encode()).hexdigest()}",
            origin=CaseOrigin(kind=CaseOriginKind.AUTHORED),
            name=case_id,
            category=category,
            grading=JudgingSpec(grader=SKILL_CANDIDATE_PROFILE, pass_threshold=0.5, rubric=rubric),
        )

    return (
        *(
            case(f"skill-line-{index}", Split.VALIDATION, CaseCategory.ESCALATION, marker)
            for index, marker in enumerate(_SKILL_MARKERS, start=1)
        ),
        case("skill-sample-content", Split.REGRESSION, CaseCategory.ESCALATION, SKILL_TEXT),
        case("skill-no-secret", Split.SAFETY, CaseCategory.SAFETY, ""),
    )


def build_rule_verifier(
    genome: Any,
    *,
    profile_name: str = "rules-file@1",
    also_default_profile: bool = True,
    include_skill_candidate: bool = False,
    suite_id: str = "platform/rules",
    grader: str = "platform/rules@1",
) -> LocalVerificationService:
    """组合一个读文件的规则验证器。

    `genome` 只需要 07 用到的两个方法：`resolve_revision(bot, ref)` 与
    `snapshot_files(revision, bot=bot)`。同一个 revision 只读取一次，避免
    每个用例都回源（HTTP 提供者下这是 5×2 次请求变成 2 次）。
    """

    cases = rule_cases(suite_id=suite_id, grader=grader)
    #: (owner, bot, revision) → transcript digest; grades read by the exact observed content.
    snapshots: dict[tuple[str, str, str, str], str] = {}
    texts: dict[str, str] = {}

    async def cases_for(bot: Any, splits: Any) -> list[Case]:
        del bot
        return [item for item in cases if item.split in splits]

    skill_cases = _skill_cases() if include_skill_candidate else ()

    async def skill_cases_for(bot: Any, splits: Any) -> list[Case]:
        del bot
        return [item for item in skill_cases if item.split in splits]

    async def _transcript(bot: Any, revision_id: str, case: Case) -> str:
        target = SKILL_FILE if include_skill_candidate and case.suite_id == _SKILL_SUITE else ""
        key = (bot.owner_id, bot.bot_id, revision_id, target)
        # Built-in data access must pass the live permission check on every
        # read, including verification after restart or collaborator removal.
        scoped = current_data_scope.get() is not None
        cached = None if scoped else snapshots.get(key)
        if cached is not None:
            return cached
        revision = await genome.resolve_revision(bot, revision_id)
        files = await genome.snapshot_files(revision, bot=bot)
        text = (
            files.get(target, b"").decode("utf-8")
            if target
            else "\n".join(value.decode("utf-8", "replace") for value in files.values())
        )
        ref = f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"
        texts[ref] = text
        if not scoped:
            snapshots[key] = ref
        return ref

    async def execute(bot: Any, revision_id: str, case: Case, seed: int) -> RolloutRecord:
        return RolloutRecord(
            case_id=case.case_id,
            revision_id=revision_id,
            seed=seed,
            executor=ExecutorKind.LOCAL_SANDBOX,
            transcript_ref=await _transcript(bot, revision_id, case),
        )

    async def grade(case: Case, rollout: RolloutRecord) -> Grade:
        if rollout.transcript_ref is None:
            raise ValueError("rule rollout requires an observed transcript")
        text = texts[rollout.transcript_ref]
        if case.category is CaseCategory.SAFETY:
            leaked = any(marker in text for marker in _SECRET_MARKERS)
            return Grade(score=0.0 if leaked else 1.0, critique="secret scan")
        if include_skill_candidate and case.suite_id == _SKILL_SUITE:
            if case.split is Split.REGRESSION:
                # This is a fixed fixture, not a general Skill quality evaluator.
                # Missing in the parent is valid; any different sample is a regression.
                valid = not text or text == case.grading.rubric
                return Grade(score=float(valid), critique="fixed sample Skill content")
            return Grade(score=float(case.grading.rubric in text.splitlines()), critique="sample Skill line")
        marker = case.grading.rubric
        present = not marker or marker in text
        return Grade(
            score=1.0 if present else 0.0,
            critique=f"marker {'present' if present else 'missing'}",
        )

    profile = VerificationProfile(
        name=profile_name,
        splits=(Split.VALIDATION, Split.REGRESSION, Split.SAFETY),
        seeds=SeedPolicy(default=1, escalate_within=0.0, max=1),
        validation=ValidationRule(min_effect=0.02, confidence=0.95),
        regression_tolerance=0,
    )
    profiles = {profile_name: profile}
    if also_default_profile and profile_name != "default@1":
        profiles["default@1"] = replace(profile, name="default@1")
    if include_skill_candidate:
        if profile_name == SKILL_CANDIDATE_PROFILE or suite_id == _SKILL_SUITE:
            raise ValueError("Skill candidate verification profile and suite are reserved")
        profiles[SKILL_CANDIDATE_PROFILE] = replace(profile, name=SKILL_CANDIDATE_PROFILE)
    return LocalVerificationService(
        profile_cases_for={SKILL_CANDIDATE_PROFILE: skill_cases_for} if include_skill_candidate else {},
        profiles=profiles,
        cases_for=cases_for,
        execute=execute,
        grade=grade,
        default_profile=profile_name,
    )
