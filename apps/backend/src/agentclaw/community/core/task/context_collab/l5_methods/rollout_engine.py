"""Durable serial rollout driver and independent authenticated progress ingress."""

import asyncio
from dataclasses import asdict, replace
from time import time
from uuid import uuid4
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.progress_event import (
    ProgressEvent,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum as S,
    EventTypeEnum as E,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    text,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RolloutRepository,
    RolloutExecutionPort,
    ProductRuntimeAuthorization,
    ReportContext,
    SessionRoute,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_lifecycle import (
    replace_stage,
    apply_progress,
    TERMINAL,
)
import json
from .multi_rollout_capabilities import UnavailableMultiRolloutIsolation
from .multi_rollout_state import candidate_for, stage_rollout, transitions, project
from .multi_rollout_execution import (
    active_rollout,
    plan_stage,
    reserve_dispatch,
    isolated_input,
    release_finished,
)


class _RetryAllocation(Exception):
    pass


class RolloutEngine:
    def __init__(
        self,
        repository: RolloutRepository,
        execution: RolloutExecutionPort,
        authorization: ProductRuntimeAuthorization,
        isolation=UnavailableMultiRolloutIsolation(),
    ) -> None:
        self.repository, self.execution, self.authorization = (
            repository,
            execution,
            authorization,
        )

        self.isolation = isolation

    async def advance(self, collet_id: int, context, *, rollout_id: int = 0) -> None:
        for _ in range(32):
            try:
                return await self._advance(collet_id, context, rollout_id)
            except _RetryAllocation:
                continue
        raise Conflict("candidate allocation contention; retry without dispatch")

    async def _advance(self, collet_id, context, rollout_id):
        snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
        self.authorization.check("advance", context, snapshot.owner)
        if snapshot.stopped:
            return
        if snapshot.multi:
            if snapshot.multi.phase in {"SUCCEEDED", "FAILED", "STOPPED"}:
                return
            current_quota = await asyncio.to_thread(
                self.isolation.require,
                snapshot.multi.policy.isolation_ref,
                snapshot.execution_ref,
                snapshot.specs,
                context,
            )
            if type(current_quota) is not int or not 1 <= current_quota <= 8:
                raise Invalid("installed tenant quota must be in 1..8")
            if int(time()) >= snapshot.multi.deadline:
                intents = transitions(snapshot, int(time()))
                await asyncio.to_thread(
                    self.repository.save,
                    snapshot,
                    snapshot.revision,
                    intents=tuple(intents),
                    release_slots=release_finished(snapshot),
                )
                return
        rollout = active_rollout(snapshot, rollout_id)
        if rollout.status in TERMINAL:
            return
        for stage in rollout.stages:
            record = next(
                r for r in snapshot.records if r.stage_id == stage.stage_rollout_id
            )
            if record.dispatch_state == "unknown" and record.route is None:
                raise Conflict(
                    "dispatch outcome unknown; explicit reconciliation required"
                )
            if record.route and stage.status not in TERMINAL:
                self.execution.require(
                    snapshot.execution_ref, snapshot.acceptance_ref, snapshot.specs
                )
                state = await self.execution.observe(record.route)
                if state not in {
                    S.RUNNING.value,
                    S.RUN_FINISHED.value,
                    S.RUN_FAILED.value,
                }:
                    raise Invalid("unrecognized runtime observation")
                observed = S(state)
                if stage.status is S.RUN_FINISHED and observed is S.RUNNING:
                    raise Conflict("runtime observation would regress a completed run")
                if observed is S.RUN_FAILED:
                    replace_stage(
                        snapshot,
                        replace(
                            stage,
                            status=S.RUN_FAILED,
                            error_msg="runtime reported execution failure",
                        ),
                    )
                else:
                    replace_stage(snapshot, replace(stage, status=observed))
                record.lease_until = int(time()) + 900
                intents = (
                    [{"kind": "advance-cand", "rollout_id": rollout.rollout_id}]
                    if snapshot.multi and observed is S.RUNNING
                    else ([{"kind": "advance"}] if observed is S.RUNNING else [])
                )
                if snapshot.multi:
                    intents.extend(transitions(snapshot, int(time())))
                try:
                    await asyncio.to_thread(
                        self.repository.save,
                        snapshot,
                        snapshot.revision,
                        intents=tuple(intents),
                        release_slots=release_finished(snapshot),
                    )
                except Conflict:
                    raise _RetryAllocation() from None
                return
        by_name = {s.name: s for s in rollout.stages}
        ready = next(
            (
                s
                for s in snapshot.specs
                if by_name[s.name].status is S.CREATE
                and all(by_name[d].status is S.ACCEPTANCE_SUCCEED for d in s.depends_on)
            ),
            None,
        )
        if ready is None:
            return
        instruction = ""
        if snapshot.multi:
            ready, instruction = plan_stage(snapshot, rollout, ready)
        selected = None
        for candidate in ready.candidates:
            try:
                self.authorization.check_candidate(candidate, context, snapshot)
            except Forbidden:
                continue
            selected = candidate
            break
        if selected is None:
            raise Forbidden("no candidate has actual execution authority")
        self.execution.require(
            snapshot.execution_ref, snapshot.acceptance_ref, snapshot.specs
        )
        stage = by_name[ready.name]
        data = {
            "context": rollout.content,
            "predecessor_artifacts": {
                d: [asdict(a) for a in by_name[d].artifacts] for d in ready.depends_on
            },
        }
        acquire_slot = None
        if snapshot.multi:
            acquire, stopped = reserve_dispatch(snapshot, rollout)
            if snapshot.multi.phase in {"SUCCEEDED", "FAILED", "STOPPED"}:
                await asyncio.to_thread(
                    self.repository.save,
                    snapshot,
                    snapshot.revision,
                    intents=tuple(stopped),
                    release_slots=release_finished(snapshot),
                )
                return
            if acquire:
                acquire_slot = (
                    rollout.rollout_id,
                    min(snapshot.multi.tenant_quota, current_quota),
                )
            data["multi_rollout"] = await isolated_input(
                self.isolation, snapshot, rollout, selected, instruction, context
            )
        content = json.dumps(data, ensure_ascii=False)
        text(content)
        record = next(
            r for r in snapshot.records if r.stage_id == stage.stage_rollout_id
        )
        record.attempt, record.lease_until, record.dispatch_state = (
            uuid4().hex,
            int(time()) + 900,
            "unknown",
        )
        replace_stage(
            snapshot,
            replace(
                stage,
                assignee_type=selected.agent_type,
                assignee_uuid=selected.agent_id,
                input=content,
            ),
        )
        # Slot, dispatch budget and unknown-delivery fence commit before IO.
        project(snapshot)
        try:
            await asyncio.to_thread(
                self.repository.save,
                snapshot,
                snapshot.revision,
                intents=(
                    {
                        "kind": "reconcile",
                        "stage_id": stage.stage_rollout_id,
                        "attempt": record.attempt,
                    },
                ),
                acquire_slot=acquire_slot,
            )
        except Conflict:
            raise _RetryAllocation() from None
        route = await self.execution.start(
            snapshot.execution_ref, selected, content, record.attempt, context
        )
        if type(route) is not SessionRoute or any(
            type(v) is not str or not v.strip() for v in asdict(route).values()
        ):
            raise Invalid("executor did not return a complete real session route")
        if snapshot.multi:
            try:
                await self.isolation.verify_route(
                    snapshot.multi.policy.isolation_ref,
                    candidate_for(snapshot, rollout.rollout_id).namespace,
                    route,
                    context,
                )
            except BaseException:
                await asyncio.to_thread(
                    self.repository.late_delivery,
                    collet_id,
                    stage.stage_rollout_id,
                    record.attempt,
                    route,
                )
                raise
        # A sibling's revision update must NOT revoke this real delivery. Retry
        # only the route attachment, never start another provider run.
        for _ in range(32):
            current = await asyncio.to_thread(self.repository.get_collet, collet_id)
            live = next(
                r for r in current.records if r.stage_id == stage.stage_rollout_id
            )
            valid = (
                not current.stopped
                and live.attempt == record.attempt
                and live.dispatch_state != "revoked"
            )
            if current.multi:
                valid = (
                    valid
                    and current.multi.phase not in {"SUCCEEDED", "FAILED", "STOPPED"}
                    and candidate_for(current, rollout.rollout_id).generation
                    == current.multi.generation
                    and int(time()) < current.multi.deadline
                )
            else:
                valid = valid and current.current_rollout_id == rollout.rollout_id
            if not valid:
                await asyncio.to_thread(
                    self.repository.late_delivery,
                    collet_id,
                    stage.stage_rollout_id,
                    record.attempt,
                    route,
                )
                raise Conflict(
                    "dispatch completed after revocation; cancellation reconciliation recorded"
                )
            live.route, live.dispatch_state = route, "delivered"
            active_roll = active_rollout(current, rollout.rollout_id)
            active = next(
                s
                for s in active_roll.stages
                if s.stage_rollout_id == stage.stage_rollout_id
            )
            replace_stage(current, replace(active, session_id=route.session_id))
            intent = (
                {"kind": "advance-cand", "rollout_id": rollout.rollout_id}
                if current.multi
                else {"kind": "advance"}
            )
            try:
                await asyncio.to_thread(
                    self.repository.save, current, current.revision, intents=(intent,)
                )
                return
            except Invalid:
                # A real delivery exists even when isolation/session invariants
                # reject attachment. Preserve it for cancellation, never lose it.
                await asyncio.to_thread(
                    self.repository.late_delivery,
                    collet_id,
                    stage.stage_rollout_id,
                    record.attempt,
                    route,
                )
                raise
            except Conflict:
                if current.multi:
                    continue
                await asyncio.to_thread(
                    self.repository.late_delivery,
                    collet_id,
                    stage.stage_rollout_id,
                    record.attempt,
                    route,
                )
                raise
        await asyncio.to_thread(
            self.repository.late_delivery,
            collet_id,
            stage.stage_rollout_id,
            record.attempt,
            route,
        )
        raise Conflict("delivery attachment contention; cancellation recorded")

    async def report(self, event: ProgressEvent, context: ReportContext) -> dict:
        # Re-validate detached mutable lists, even for typed in-process callers.
        event = ProgressEvent.from_dict(event.to_dict())
        positive_id(context.expected_revision)
        text(context.request_id, maximum=128)
        text(context.attempt, maximum=128)
        snapshot = await asyncio.to_thread(
            self.repository.locate, event.stage_rollout_id, "stage"
        )
        self.authorization.check("report", context.actor, snapshot.owner)
        payload = {
            "event": event.to_dict(),
            "attempt": context.attempt,
            "revision": context.expected_revision,
        }
        replay = await asyncio.to_thread(
            self.repository.replay,
            context.request_id,
            "progress",
            payload,
            context.actor,
        )
        if replay is not None:
            return replay
        if snapshot.multi and int(time()) >= snapshot.multi.deadline:
            raise Conflict(
                "controller deadline expired; progress cannot revive execution"
            )
        if snapshot.revision != context.expected_revision or snapshot.stopped:
            raise Conflict("stale or stopped execution")
        rollout = stage_rollout(snapshot, event.stage_rollout_id)
        stage = next(
            (s for s in rollout.stages if s.stage_rollout_id == event.stage_rollout_id),
            None,
        )
        record = next(
            r for r in snapshot.records if r.stage_id == event.stage_rollout_id
        )
        if (
            stage is None
            or record.attempt != context.attempt
            or record.lease_until < int(time())
            or record.dispatch_state != "delivered"
            or record.route is None
        ):
            raise Conflict("historical, revoked, unbound or expired allocation")
        if event.type is E.RUN_FAILED:
            actor = context.actor.actor
            if (actor.principal_type, actor.principal_id) != (
                stage.assignee_type,
                stage.assignee_uuid,
            ):
                raise Forbidden(
                    "failure fact must come from the actual allocated executor"
                )
        else:
            spec = next(s for s in snapshot.specs if s.name == stage.name)
            await self.execution.verify(
                snapshot.acceptance_ref,
                record.route,
                spec.conditions,
                event.artifacts,
                event.acceptances,
                context.actor,
            )
        if snapshot.multi:
            candidate = candidate_for(snapshot, rollout.rollout_id, active=True)
            await self.isolation.verify_artifacts(
                snapshot.multi.policy.isolation_ref,
                candidate.namespace,
                event.artifacts,
                context.actor,
            )
        apply_progress(snapshot, event)
        intents = []
        if snapshot.multi:
            intents.extend(transitions(snapshot, int(time())))
            if event.type is E.ACCEPTANCE_SUCCEED and rollout.status not in TERMINAL:
                intents.append(
                    {"kind": "advance-cand", "rollout_id": rollout.rollout_id}
                )
        elif event.type is E.ACCEPTANCE_SUCCEED:
            intents.append({"kind": "advance"})
        result = {
            "accepted": True,
            "stage_rollout_id": event.stage_rollout_id,
            "type": event.type.value,
            "revision": snapshot.revision + 1,
        }
        return await asyncio.to_thread(
            self.repository.save,
            snapshot,
            context.expected_revision,
            receipt=(context.request_id, "progress", payload, context.actor, result),
            intents=tuple(intents),
            release_slots=release_finished(snapshot),
        )
