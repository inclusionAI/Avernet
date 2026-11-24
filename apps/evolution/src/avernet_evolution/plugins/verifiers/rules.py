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

__all__ = ["RULE_VALIDATION_MARKERS", "build_rule_verifier", "rule_cases"]

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


def build_rule_verifier(
    genome: Any,
    *,
    profile_name: str = "pending@1",
    also_default_profile: bool = True,
    suite_id: str = "platform/rules",
    grader: str = "platform/rules@1",
) -> LocalVerificationService:
    """组合一个读文件的规则验证器。

    `genome` 只需要 07 用到的两个方法：`resolve_revision(bot, ref)` 与
    `snapshot_files(revision, bot=bot)`。同一个 revision 只读取一次，避免
    每个用例都回源（HTTP 提供者下这是 5×2 次请求变成 2 次）。
    """

    cases = rule_cases(suite_id=suite_id, grader=grader)
    #: (owner, bot, revision) → 该修订版的全部文件文本
    texts: dict[tuple[str, str, str], str] = {}
    #: (revision, seed) → 本次 rollout 读到的文本（grade 只拿到 rollout）
    rollout_texts: dict[tuple[str, int], str] = {}

    async def cases_for(bot: Any, splits: Any) -> list[Case]:
        del bot
        return [item for item in cases if item.split in splits]

    async def _text(bot: Any, revision_id: str) -> str:
        key = (bot.owner_id, bot.bot_id, revision_id)
        cached = texts.get(key)
        if cached is not None:
            return cached
        revision = await genome.resolve_revision(bot, revision_id)
        files = await genome.snapshot_files(revision, bot=bot)
        text = "\n".join(value.decode("utf-8", "replace") for value in files.values())
        texts[key] = text
        return text

    async def execute(bot: Any, revision_id: str, case: Case, seed: int) -> RolloutRecord:
        text = await _text(bot, revision_id)
        rollout_texts[(revision_id, seed)] = text
        return RolloutRecord(
            case_id=case.case_id,
            revision_id=revision_id,
            seed=seed,
            executor=ExecutorKind.LOCAL_SANDBOX,
            transcript_ref=f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
        )

    async def grade(case: Case, rollout: RolloutRecord) -> Grade:
        text = rollout_texts.get((rollout.revision_id, rollout.seed), "")
        if case.category is CaseCategory.SAFETY:
            leaked = any(marker in text for marker in _SECRET_MARKERS)
            return Grade(score=0.0 if leaked else 1.0, critique="secret scan")
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
    return LocalVerificationService(
        profiles=profiles,
        cases_for=cases_for,
        execute=execute,
        grade=grade,
        default_profile=profile_name,
    )
