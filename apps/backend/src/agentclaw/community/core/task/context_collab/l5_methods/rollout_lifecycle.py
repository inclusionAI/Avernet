"""Single product-rollout transition authority. SQL performs revision arbitration."""

from dataclasses import replace
from typing import Callable
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum as S,
    EventTypeEnum as E,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.progress_event import (
    ProgressEvent,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.stage_rollout import (
    StageRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RuntimeSnapshot,
    StageRecord,
)

TERMINAL = {S.RUN_FAILED, S.ACCEPTANCE_FAILED, S.ACCEPTANCE_SUCCEED}


def append_rollout(
    snapshot: RuntimeSnapshot,
    identity: Callable[[str], int],
    content: str,
    affected: set[str],
) -> None:
    previous = snapshot.current() if snapshot.rollouts else None
    rid = identity("rollout")
    stages = []
    for spec in snapshot.specs:
        sid = identity("stage")
        old = (
            next((s for s in previous.stages if s.name == spec.name), None)
            if previous
            else None
        )
        if old and spec.name not in affected and old.status is S.ACCEPTANCE_SUCCEED:
            stage = replace(old, stage_rollout_id=sid, session_id="")
            record = StageRecord(
                sid, reused_from=old.stage_rollout_id, dispatch_state="reused"
            )
        else:
            stage = StageRollout(
                spec.name, sid, S.CREATE, "", "", content, "", [], [], ""
            )
            record = StageRecord(sid)
        stages.append(stage)
        snapshot.records.append(record)
    snapshot.rollouts.append(
        ColletRollout(snapshot.collet.id, rid, stages, content, S.CREATE)
    )
    if previous:
        snapshot.parents[str(rid)] = previous.rollout_id
    snapshot.current_rollout_id = rid
    snapshot.stopped = False
    aggregate(snapshot)


def affected_stages(snapshot: RuntimeSnapshot, selected: tuple[str, ...]) -> set[str]:
    names = {s.name for s in snapshot.specs}
    if (
        not selected
        or len(set(selected)) != len(selected)
        or not set(selected) <= names
    ):
        raise Invalid("adjustment must reference existing unique stage names")
    affected = set(selected)
    while True:
        expanded = affected | {
            s.name for s in snapshot.specs if set(s.depends_on) & affected
        }
        if expanded == affected:
            return affected
        affected = expanded


def aggregate(snapshot: RuntimeSnapshot, rollout_id: int = 0) -> None:
    rollout = next(
        r
        for r in snapshot.rollouts
        if r.rollout_id == (rollout_id or snapshot.current_rollout_id)
    )
    states = {s.status for s in rollout.stages}
    if S.RUN_FAILED in states:
        status = S.RUN_FAILED
    elif S.ACCEPTANCE_FAILED in states:
        status = S.ACCEPTANCE_FAILED
    elif states == {S.ACCEPTANCE_SUCCEED}:
        status = S.ACCEPTANCE_SUCCEED
    elif states == {S.CREATE}:
        status = S.CREATE
    elif states <= {S.RUN_FINISHED, S.ACCEPTANCE_SUCCEED}:
        status = S.RUN_FINISHED
    else:
        status = S.RUNNING
    snapshot.rollouts[snapshot.rollouts.index(rollout)] = replace(
        rollout, status=status
    )
    if snapshot.multi:
        from .multi_rollout_state import project

        project(snapshot)
        return
    # Stage acceptance cannot impersonate a separate owner's root acceptance.
    collet_status = (
        S.RUN_FINISHED
        if status is S.ACCEPTANCE_SUCCEED and snapshot.overall_acceptance == "owner"
        else status
    )
    snapshot.collet = replace(snapshot.collet, status=collet_status)


def replace_stage(snapshot: RuntimeSnapshot, stage: StageRollout) -> None:
    from .multi_rollout_state import stage_rollout

    rollout = stage_rollout(snapshot, stage.stage_rollout_id)
    stages = [
        stage if s.stage_rollout_id == stage.stage_rollout_id else s
        for s in rollout.stages
    ]
    if all(s.stage_rollout_id != stage.stage_rollout_id for s in rollout.stages):
        raise Conflict("stage does not belong to the current rollout")
    snapshot.rollouts[snapshot.rollouts.index(rollout)] = replace(
        rollout, stages=stages
    )
    aggregate(snapshot, rollout.rollout_id)


def apply_progress(snapshot: RuntimeSnapshot, event: ProgressEvent) -> None:
    from .multi_rollout_state import stage_rollout

    rollout = stage_rollout(snapshot, event.stage_rollout_id)
    stage = next(
        (s for s in rollout.stages if s.stage_rollout_id == event.stage_rollout_id),
        None,
    )
    if stage is None or snapshot.stopped or stage.status in TERMINAL:
        raise Conflict("stage is stopped, historical or already terminal")
    spec = next(s for s in snapshot.specs if s.name == stage.name)
    if event.type is E.RUN_FAILED:
        if stage.status not in {S.RUNNING, S.RUN_FINISHED}:
            raise Conflict("run failure requires an actual allocation")
        status = S.RUN_FAILED
    else:
        if stage.status is not S.RUN_FINISHED:
            raise Conflict("acceptance requires observed runtime completion")
        conditions = [r.condition for r in event.acceptances]
        if len(set(conditions)) != len(conditions) or set(conditions) != set(
            spec.conditions
        ):
            raise Invalid("acceptance must cover exactly the frozen conditions")
        if event.type is E.ACCEPTANCE_SUCCEED:
            if not all(r.passed for r in event.acceptances):
                raise Invalid("successful acceptance contains a failed condition")
            status = S.ACCEPTANCE_SUCCEED
        else:
            if all(r.passed for r in event.acceptances):
                raise Invalid("failed acceptance needs a failed condition and reason")
            status = S.ACCEPTANCE_FAILED
    replace_stage(
        snapshot,
        replace(
            stage,
            status=status,
            artifacts=event.artifacts,
            acceptances=event.acceptances,
            error_msg=event.error_msg,
        ),
    )
