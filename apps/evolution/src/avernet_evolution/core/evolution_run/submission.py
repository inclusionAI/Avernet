"""候选提交流水线（06 §5.4）：底线 → 记录修订版 → 实验记录 → 验证。

`RunStrategyContext` 与作业通道（容器内的 worker）都只经由此函数提交候选，
因此底线、去重与记录的顺序只有一份实现。
"""

from __future__ import annotations

from typing import Any

from avernet_evolution.core.evolution_strategy import Candidate, check_allowed_genes

from .budget import BudgetUsage
from .errors import CandidateRejected
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

    from avernet_evolution.core.evolution_strategy import candidate_id_of

    candidate_id = candidate_id_of(candidate)
    row = store.reserve_candidate(run.id, candidate_id)
    if row.verdict == "reject" or store.submission_complete(run.id, candidate_id):
        return candidate_id

    seq = row.seq
    revision = row.revision
    if not revision:
        # An empty placeholder is an interrupted submission, not a successful one.
        floor = await promotion.check_floor(run.bot, candidate_id) if promotion is not None else None
        if floor is not None and floor.result == "fail":
            store.upsert_candidate(run.id, candidate_id, "", seq=seq, verdict="reject")
            return candidate_id

        # Registry is idempotent for (bot, run, patch), including a lost response
        # after its transaction committed. Never manufacture a second run/ref.
        try:
            revision = await genome.commit_patch(
                run.bot,
                patch,
                run.id,
                run.strategy_ref,
                tuple(run.allowed_genes),
                run.strategy_ref,
            )
        except CandidateRejected as exc:
            store.events.append(
                "candidate.rejected",
                run_id=run.id,
                details={"candidate_id": candidate_id, "rejection_code": exc.code.value},
            )
            # Keep the unrecorded placeholder distinct from a persisted candidate
            # or an evaluation verdict. Never manufacture a revision/Ledger entry.
            raise

    # Also reconciles historical rows whose revision was saved but count was not.
    store.record_candidate_revision(run.id, candidate_id, revision)

    store.events.append(
        "candidate.persisted", run_id=run.id, details={"candidate_id": candidate_id, "revision": revision}
    )

    if ledger is not None:
        entry_id = await ledger.record_submission(
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
        store.events.append(
            "ledger.submission.persisted",
            run_id=run.id,
            details={"candidate_id": candidate_id, "ledger_entry_id": entry_id},
        )
    if verifier is not None:
        from avernet_evolution.core.evolution_verify import CandidateRef

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
    # Both downstream calls are idempotent. If either raises, leave this false
    # and propagate the failure so a worker retry can finish the submission.
    store.complete_submission(run.id, candidate_id)
    return candidate_id
