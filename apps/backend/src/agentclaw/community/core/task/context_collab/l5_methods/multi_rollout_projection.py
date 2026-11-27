"""Bounded query/observability projection. No feedback, credentials or authority."""

from dataclasses import asdict
from .multi_rollout_contracts import SCHEMA
from .rollout_lifecycle import TERMINAL
from ..l4_context_primitives.product_status import StatusEnum as S


def control_metadata(snapshot):
    c = snapshot.multi
    if c is None:
        return None
    scores = {
        s["rollout_id"]: s
        for d in c.decisions
        if d["kind"] == "selection"
        for s in d["scores"]
    }
    return dict(
        schema=SCHEMA,
        run_id=c.run_id,
        phase=c.phase,
        outcome=c.outcome,
        reason=c.reason,
        strategy=c.policy.strategy.kind,
        generation=c.generation,
        focus=snapshot.current_rollout_id,
        winner=c.winner or None,
        created_at=c.created_at,
        deadline=c.deadline,
        waiting_until=c.waiting_until or None,
        configured=c.policy.limits.max_rollouts,
        started=sum(v.started for v in c.candidates),
        completed=sum(r.status in TERMINAL for r in snapshot.rollouts),
        accepted=sum(r.status is S.ACCEPTANCE_SUCCEED for r in snapshot.rollouts),
        incomplete=sum(v.incomplete for v in c.candidates),
        active_candidate_ids=[
            v.rollout_id
            for v in c.candidates
            if v.generation == c.generation
            and not v.incomplete
            and c.phase not in {"SUCCEEDED", "FAILED", "STOPPED"}
        ],
        limits=asdict(c.policy.limits),
        used=dict(
            rollouts=len(c.candidates),
            stage_dispatches=c.dispatches,
            strategy_calls=c.strategy_calls,
            no_progress=c.no_progress,
        ),
        reserved_slots=sum(v.slot for v in c.candidates),
        peak_concurrency=c.peak_concurrency,
        cancellations=dict(c.cancellations),
        strategy_elapsed_ms=dict(c.strategy_elapsed_ms),
        strategy_attempts=[
            {k: a[k] for k in ("kind", "generation", "ordinal", "state", "elapsed_ms")}
            for a in c.strategy_attempts
        ],
        candidates=[
            dict(
                rollout_id=v.rollout_id,
                generation=v.generation,
                ordinal=v.ordinal,
                predecessor=v.predecessor or None,
                plan_digest=v.plan_digest,
                plan_version=next(
                    p.version for p in c.plans if p.digest == v.plan_digest
                ),
                body_binding=v.binding.method_binding_id,
                namespace=v.namespace,
                started=v.started,
                slot_reserved=v.slot,
                incomplete=v.incomplete,
                selected=v.rollout_id == c.winner,
                score=scores.get(v.rollout_id),
            )
            for v in c.candidates
        ],
    )
