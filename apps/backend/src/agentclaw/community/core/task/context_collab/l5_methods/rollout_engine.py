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


class RolloutEngine:
    def __init__(
        self,
        repository: RolloutRepository,
        execution: RolloutExecutionPort,
        authorization: ProductRuntimeAuthorization,
    ) -> None:
        self.repository, self.execution, self.authorization = (
            repository,
            execution,
            authorization,
        )

    async def advance(self, collet_id: int, context) -> None:
        snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
        self.authorization.check("advance", context, snapshot.owner)
        if snapshot.stopped or snapshot.current().status in TERMINAL:
            return
        rollout = snapshot.current()
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
                await asyncio.to_thread(
                    self.repository.save,
                    snapshot,
                    snapshot.revision,
                    intents=({"kind": "advance"},) if observed is S.RUNNING else (),
                )
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
        content = json.dumps(
            {
                "context": rollout.content,
                "predecessor_artifacts": {
                    d: [asdict(a) for a in by_name[d].artifacts]
                    for d in ready.depends_on
                },
            },
            ensure_ascii=False,
        )
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
        # Persist the allocation and unknown-delivery fence before network IO.
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
        )
        route = await self.execution.start(
            snapshot.execution_ref, selected, content, record.attempt, context
        )
        if type(route) is not SessionRoute or any(
            type(v) is not str or not v.strip() for v in asdict(route).values()
        ):
            raise Invalid("executor did not return a complete real session route")
        current = await asyncio.to_thread(self.repository.get_collet, collet_id)
        live = next(r for r in current.records if r.stage_id == stage.stage_rollout_id)
        if (
            current.stopped
            or current.current_rollout_id != rollout.rollout_id
            or live.attempt != record.attempt
            or live.dispatch_state == "revoked"
        ):
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
        active = next(
            s
            for s in current.current().stages
            if s.stage_rollout_id == stage.stage_rollout_id
        )
        replace_stage(current, replace(active, session_id=route.session_id))
        try:
            await asyncio.to_thread(
                self.repository.save,
                current,
                current.revision,
                intents=({"kind": "advance"},),
            )
        except Conflict:
            await asyncio.to_thread(
                self.repository.late_delivery,
                collet_id,
                stage.stage_rollout_id,
                record.attempt,
                route,
            )
            raise

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
        if snapshot.revision != context.expected_revision or snapshot.stopped:
            raise Conflict("stale or stopped execution")
        stage = next(
            (
                s
                for s in snapshot.current().stages
                if s.stage_rollout_id == event.stage_rollout_id
            ),
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
        apply_progress(snapshot, event)
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
            intents=({"kind": "advance"},)
            if event.type is E.ACCEPTANCE_SUCCEED
            else (),
        )
