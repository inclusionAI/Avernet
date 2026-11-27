from uuid import uuid4
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    EventTypeEnum as E,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.progress_event import (
    ProgressEvent,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ArtifactContentText,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ReportContext,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductIntentRow,
)


def saved(h):
    return h.repository.get_collet(h.collet.id)


def report(h, stage, passed=True, request=None):
    snap = saved(h)
    rec = next(r for r in snap.records if r.stage_id == stage.stage_rollout_id)
    spec = next(s for s in snap.specs if s.name == stage.name)
    event = ProgressEvent(
        E.ACCEPTANCE_SUCCEED if passed else E.ACCEPTANCE_FAILED,
        stage.stage_rollout_id,
        [Artifact("doc", "actual", ArtifactContentText("verified output"))],
        [
            AcceptanceResult(c, passed, "trusted contract check")
            for c in spec.conditions
        ],
        "",
    )
    return event, ReportContext(
        h.owner, request or uuid4().hex, rec.attempt, snap.revision
    )


async def finish(h, rid, passed=True):
    h.state["value"] = "final"
    snap = saved(h)
    rollout = next(r for r in snap.rollouts if r.rollout_id == rid)
    if not any(
        r.attempt
        for r in snap.records
        if r.stage_id == rollout.stages[0].stage_rollout_id
    ):
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    stage = next(r for r in saved(h).rollouts if r.rollout_id == rid).stages[0]
    event, context = report(h, stage, passed)
    return await h.engine.report(event, context)


def ready_retries(h):
    # Simulate the host's next scheduled lease tick without arbitrary sleeps.
    with h.repository.db.transactional_orm_session() as session:
        session.query(ProductIntentRow).filter(
            ProductIntentRow.state == "requested"
        ).update({ProductIntentRow.lease_until: 0})
