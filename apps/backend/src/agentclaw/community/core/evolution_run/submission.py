"""候选提交流水线（06 §5.4）：底线 → 记录修订版 → 实验记录 → 验证。

`RunStrategyContext` 与作业通道（容器内的 worker）都只经由此函数提交候选，
因此底线、去重与记录的顺序只有一份实现。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agentclaw.community.core.evolution_strategy import Candidate, check_allowed_genes

from .budget import BudgetUsage
from .records import Run

__all__ = ["submit_candidate"]


async def submit_candidate(
    *,
    store: Any,
    genome: Any,
    run: Run,
    candidate: Candidate,
    promotion: Any = None,
    ledger: Any = None,
    verifier: Any = None,
) -> str:
    """记录一个候选并启动验证；同一补丁重入返回同一 id。"""

    patch = candidate.patch
    if patch.base != run.parent_revision:
        raise ValueError(f"patch base {patch.base!r} is not the run's parent revision")
    problems = check_allowed_genes(patch, list(run.allowed_genes), [])
    if problems:
        raise ValueError("; ".join(problems))

    from agentclaw.community.core.evolution_strategy import candidate_id_of

    candidate_id = candidate_id_of(candidate)
    if any(row.candidate_id == candidate_id for row in store.candidates(run.id)):
        return candidate_id  # 同一补丁重入返回同一 id（§5.4 第 2 步）

    # 先登记候选，使底线检查能按 id 找到它的上文（§5.4 第 3 步）
    seq = len(store.candidates(run.id)) + 1
    store.upsert_candidate(run.id, candidate_id, "", seq=seq)
    floor = (
        await promotion.check_floor(run.bot, candidate_id)
        if promotion is not None
        else None
    )
    if floor is not None and floor.result == "fail":
        store.upsert_candidate(run.id, candidate_id, "", seq=seq, verdict="reject")
        return candidate_id

    revision = await genome.commit_patch(
        run.bot,
        patch,
        run.id,
        run.strategy_ref,
        tuple(run.allowed_genes),
        run.strategy_ref,
    )

    store.upsert_candidate(run.id, candidate_id, revision, seq)
    updated = store.get_run(run.id)
    if updated is not None:
        store.update_run(replace(updated, candidate_count=seq))

    if ledger is not None:
        await ledger.record_submission(
            bot=run.bot,
            run_id=run.id,
            binding_id=run.binding_id,
            iteration=seq,
            candidate_id=candidate_id,
            mechanism=run.strategy_ref,
            parent_revision=run.parent_revision,
            candidate_revision=revision,
            evidence=list(candidate.evidence),
            patch={"ops": len(patch.ops), "rationale": patch.rationale},
            cost=BudgetUsage(),
            actor=run.strategy_ref,
        )
    if verifier is not None:
        from agentclaw.community.core.evolution_verify import CandidateRef

        await verifier.verify(
            CandidateRef(
                candidate_id=candidate_id,
                bot_owner_id=run.bot.owner_id,
                bot_id=run.bot.bot_id,
                revision=revision,
                parent_revision=run.parent_revision,
                run_id=run.id,
            ),
            run.verification_profile,
        )
    return candidate_id
