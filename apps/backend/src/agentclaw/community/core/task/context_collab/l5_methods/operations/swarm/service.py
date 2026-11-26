"""L5 Swarm authority operations and internal execution-control assembly.

SwarmMethodService assembles plan/Stage state-machine operations. The internal
SwarmExecutionOperations capability coordinates bounded Driver calls, Event
journaling and interaction revisions for the existing task-commission Service
API; it is not a separately published Service API or a second runtime.

Accepted requests/decisions are journaled before execution. Failed writes escape.
Retries reconcile accepted state, never silently dispatch another Stage. Runtime
history loss remains fail-closed rather than a claim of restart recovery.
"""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    validate_event_body,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
    CollaborationReader,
    EventWriter,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.participation import (
    validate_enrolled_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.base import (
    SwarmMethodServiceBase,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.stage import (
    _parse_timestamp,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    InteractionCommand,
    InteractionDecision,
    InteractionMode,
    InteractionPlanningRequest,
    StepCommand,
    SwarmDriverFactory,
    SwarmExecutionControl,
    SwarmInteractionPlanner,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_state import (
    current_plan,
    pending_clarifications,
    session_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_replanning import (
    build_revision,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow_ports import (
    SwarmArtifactEvidenceValidator,
    SwarmWorkflowPolicyReader,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)


from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_reads import (
    SwarmExecutionReads,
)


from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.plan import (
    SwarmPlanOpsMixin,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.stage import (
    SwarmStageOpsMixin,
)


class SwarmMethodService(SwarmStageOpsMixin, SwarmPlanOpsMixin):
    """Authoritative Method operations; external computation stays in the Driver."""


class SwarmExecutionOperations(
    SwarmExecutionReads, SwarmMethodServiceBase, SwarmExecutionControl
):
    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        authorizer: MethodOpsAuthorizerProtocol,
        collab_repo: CollaborationReader,
        workflow_policies: SwarmWorkflowPolicyReader,
        artifact_validator: SwarmArtifactEvidenceValidator,
        writer: EventWriter,
        drivers: SwarmDriverFactory,
        planner: SwarmInteractionPlanner,
    ):
        super().__init__(
            store, authorizer, collab_repo, workflow_policies, artifact_validator
        )
        self.writer, self.drivers, self.interaction_planner = writer, drivers, planner

    def _record(self, collet_id):
        record = self._collab_repo.get_current_status(collet_id)
        if record is None:
            raise ContextCollabNotFoundError("Swarm Collet not found")
        if record.task is None:
            raise ContextCollabValidationError("Swarm requires root Work")
        validate_enrolled_participation(collet_id, record.events, self._store)
        return record

    def _execution(self, collet_id, binding_id):
        axes = current_axes()
        record = self._record(collet_id)
        plan = current_plan(
            [
                p
                for p in self._store.list_plans(axes, binding_id, collet_id)
                if p.task_ref == record.task.task_id
            ]
        )
        stages = tuple(
            s
            for s in self._store.list_stages(axes, binding_id, collet_id)
            if plan is not None and s.swarm_plan_ref == plan.swarm_plan_id
        )
        return record, plan, stages

    def _owner(self, collet_id, binding_id, actor, *, write=True):
        axes = current_axes()
        binding = self._load_binding(axes, collet_id, binding_id)
        if "Swarm" not in binding.method_names:
            raise ContextCollabValidationError("binding must declare Swarm")
        if write:
            self._gate("swarm_execution", collet_id, binding_id, actor)
        else:
            self._authorize("swarm_execution_read", binding, actor, collet_id)
        record = self._record(collet_id)
        # Retain G1 hook; additionally disambiguate Principal types in P1's
        # otherwise string-only owner/executor comparison.
        allowed = actor.actor == record.task.principal_ref or any(
            (a.agent_type, a.agent_id)
            == (actor.actor.principal_type, actor.actor.principal_id)
            for a in record.task.executor_refs
        )
        if not allowed:
            raise ContextCollabValidationError(
                "actor is not the root principal/executor"
            )
        if write and record.task.status not in {"created", "in_progress"}:
            raise ContextCollabValidationError("root Work is no longer executable")
        return record

    def _worker(self, collet_id, binding_id, stage, actor, *, replay=False):
        axes = current_axes()
        binding = self._load_binding(axes, collet_id, binding_id)
        record = self._record(collet_id)
        if (
            binding.state.value != "ACTIVE"
            or "Swarm" not in binding.method_names
            or (not replay and stage.state is not StageState.RUNNING)
        ):
            raise ContextCollabValidationError(
                "clarification requires running Swarm Stage"
            )
        allocations = [
            a
            for a in self._store.list_allocations(axes, binding_id, collet_id)
            if a.stage_ref == stage.stage_id
            and (
                replay
                or (
                    a.state.value == "ACTIVE"
                    and _parse_timestamp(
                        a.lease_expires_at, "allocation", "lease_expires_at"
                    )
                    > datetime.now(timezone.utc)
                )
            )
        ]
        participants = {
            p.participant_id: p
            for p in current_participation(record.events).participants
        }
        allowed = [
            a
            for a in allocations
            if a.target_ref in participants
            and participants[a.target_ref].agent_ref is not None
            and (
                participants[a.target_ref].agent_ref.agent_type,
                participants[a.target_ref].agent_ref.agent_id,
            )
            == (actor.actor.principal_type, actor.actor.principal_id)
        ]
        if len(allowed) != 1:
            raise ContextCollabValidationError("actor is not the live allocated Agent")
        self._authorizer.check_swarm_op(
            "worker_interaction", binding, actor, principal_id=actor.actor.principal_id
        )

    def _append(
        self, collet_id, kind, payload, actor, *, expected_version: int | None = None
    ):
        """Initial/decision writes fence their exact snapshot; followups use fresh CAS."""
        record = self._record(collet_id)
        if expected_version is not None and record.collet.version != expected_version:
            raise ContextCollabVersionConflict("stale Swarm append snapshot")
        if "result" in payload:
            payload["result"]["version"] = record.collet.version + 1
        validate_event_body(
            event_kind=kind,
            primitive_type="Message",
            visibility="workspace",
            payload=payload,
            internal_execution=True,
        )
        return self.writer.append_event(
            collet_id,
            AppendEventRecordRequest(
                record.collet.version,
                kind,
                "Message",
                actor.actor,
                record.events[-1].event_id,
                "workspace",
                payload,
            ),
        ).event

    @staticmethod
    def _version(record, command):
        if record.collet.version != command.expected_version:
            raise ContextCollabVersionConflict("stale Swarm request revision")

    @staticmethod
    def _events(record, binding_id, request_id):
        return [
            e
            for e in record.events
            if e.event_kind.startswith("swarm.")
            and (e.payload or {}).get("binding_id") == binding_id
            and e.payload.get("request_id") == request_id
        ]

    @staticmethod
    def _replay(events, command, actor):
        if events and (
            events[0].payload.get("command") != asdict(command)
            or events[0].actor_ref != actor.actor
        ):
            raise ContextCollabVersionConflict(
                "request_id replay changed command or actor"
            )

    @staticmethod
    def _unfinished(record, binding_id, except_id=""):
        relevant = [
            e
            for e in record.events
            if (e.payload or {}).get("binding_id") == binding_id
        ]
        started = {
            e.payload["request_id"]
            for e in relevant
            if e.event_kind in {"swarm.step.requested", "swarm.interaction.posted"}
        }
        finished = {
            e.payload["request_id"]
            for e in relevant
            if e.event_kind in {"swarm.step.completed", "swarm.interaction.applied"}
        }
        if started - finished - {except_id}:
            raise ContextCollabVersionConflict(
                "retry the outstanding Swarm request before another mutation"
            )

    def step(
        self, collet_id: str, binding_id: str, command: StepCommand, actor: ActorContext
    ) -> dict:
        with self._store.swarm_guard(current_axes(), collet_id):
            record = self._owner(collet_id, binding_id, actor, write=False)
            events = self._events(record, binding_id, command.request_id)
            self._replay(events, command, actor)
            if events and events[-1].event_kind == "swarm.step.completed":
                return {
                    **events[-1].payload["result"],
                    "event_ref": events[-1].event_id,
                }
            self._owner(collet_id, binding_id, actor)
            self._unfinished(record, binding_id, command.request_id)
            if not events:
                self._version(record, command)
                before = self._store.list_allocations(
                    current_axes(), binding_id, collet_id
                )
                self._append(
                    collet_id,
                    "swarm.step.requested",
                    {
                        "binding_id": binding_id,
                        "request_id": command.request_id,
                        "command": asdict(command),
                        "allocation_refs": [a.allocation_id for a in before],
                    },
                    actor,
                    expected_version=command.expected_version,
                )
            else:
                before = events[0].payload["allocation_refs"]
            # If any allocation was accepted since this request, never redrive it,
            # including after a dispatch/result-journal failure.
            refs = set(before if events else [a.allocation_id for a in before])
            allocations = self._store.list_allocations(
                current_axes(), binding_id, collet_id
            )
            new = [a for a in allocations if a.allocation_id not in refs]
            dispatch_returned = any(
                e.event_kind == "swarm.step.dispatched" for e in events
            )
            if not new:
                driver = self.drivers.build(collet_id)
                if driver.binding_id != binding_id:
                    raise ContextCollabValidationError(
                        "step binding differs from driver binding"
                    )
                driver.advance_stage()
                allocations = self._store.list_allocations(
                    current_axes(), binding_id, collet_id
                )
                new = [a for a in allocations if a.allocation_id not in refs]
                if new:
                    self._append(
                        collet_id,
                        "swarm.step.dispatched",
                        {
                            "binding_id": binding_id,
                            "request_id": command.request_id,
                            "allocation_refs": [a.allocation_id for a in new],
                        },
                        actor,
                    )
                    dispatch_returned = True
            projection = self._project(collet_id, binding_id)
            result = {
                "request_id": command.request_id,
                "stage_ref": new[0].stage_ref
                if new
                else next(
                    (
                        s["stage_id"]
                        for s in projection["stages"]
                        if s["state"] in {"RUNNING", "ALLOCATING"}
                    ),
                    None,
                ),
                "status": "RECONCILIATION_REQUIRED"
                if new and not dispatch_returned
                else projection["status"],
                "plan_ref": projection["plan_ref"],
                "dispatched": bool(new) and dispatch_returned,
                "allocation_accepted": bool(new),
                "dispatch_state": "returned"
                if new and dispatch_returned
                else ("unknown" if new else "none"),
                "version": self._record(collet_id).collet.version + 1,
            }
            event = self._append(
                collet_id,
                "swarm.step.completed",
                {
                    "binding_id": binding_id,
                    "request_id": command.request_id,
                    "result": result,
                },
                actor,
            )
            return {**result, "event_ref": event.event_id}

    def interact(
        self,
        collet_id: str,
        binding_id: str,
        command: InteractionCommand,
        actor: ActorContext,
    ) -> dict:
        with self._store.swarm_guard(current_axes(), collet_id):
            record, plan, stages = self._execution(collet_id, binding_id)
            events = self._events(record, binding_id, command.request_id)
            self._replay(events, command, actor)
            # Resolve target by stored identity for replay too; never accept arbitrary sessions.
            stage = self._store.get_stage(
                current_axes(), command.stage_ref, binding_id, collet_id
            )
            if stage is None or command.session_ref != session_ref(
                binding_id, stage.stage_id
            ):
                raise ContextCollabNotFoundError(
                    "Stage/session not found in this Swarm binding"
                )
            if command.kind == "clarification_request":
                self._worker(collet_id, binding_id, stage, actor, replay=bool(events))
            else:
                self._owner(
                    collet_id,
                    binding_id,
                    actor,
                    write=not (
                        events
                        and events[-1].event_kind
                        in {"swarm.interaction.applied", "swarm.clarification.replied"}
                    ),
                )
            if events and events[-1].event_kind in {
                "swarm.interaction.applied",
                "swarm.clarification.requested",
                "swarm.clarification.replied",
            }:
                return events[-1].payload.get(
                    "result",
                    {
                        "event_ref": events[-1].event_id,
                        "kind": command.kind,
                        "version": events[-1].context_version_after,
                    },
                )
            applied_revision = (
                plan.termination_condition.get("execution_revision", {}).get(
                    "interaction_ref"
                )
                if plan
                else None
            )
            continuing_revision = events and applied_revision == events[0].event_id
            if plan is None or (
                stage.swarm_plan_ref != plan.swarm_plan_id and not continuing_revision
            ):
                raise ContextCollabVersionConflict(
                    "interaction targets superseded execution"
                )
            if command.kind != "clarification_request":
                self._unfinished(record, binding_id, command.request_id)
            if not events:
                self._version(record, command)
                payload = {
                    **asdict(command),
                    "binding_id": binding_id,
                    "command": asdict(command),
                }
                if command.kind == "clarification_reply":
                    if command.clarification_ref not in {
                        e.event_id
                        for e in pending_clarifications(
                            record.events, binding_id, stage.stage_id
                        )
                    }:
                        raise ContextCollabValidationError(
                            "clarification is not pending in this session"
                        )
                    kind = "swarm.clarification.replied"
                elif command.kind == "clarification_request":
                    if pending_clarifications(
                        record.events, binding_id, stage.stage_id
                    ):
                        raise ContextCollabVersionConflict(
                            "Stage already has a pending clarification"
                        )
                    kind = "swarm.clarification.requested"
                    payload["recipient"] = record.task.principal_ref.to_dict()
                else:
                    kind = "swarm.interaction.posted"
                posted = self._append(
                    collet_id,
                    kind,
                    payload,
                    actor,
                    expected_version=command.expected_version,
                )
                events = [posted]
                if command.kind != "message":
                    return {
                        "event_ref": posted.event_id,
                        "kind": command.kind,
                        "version": posted.context_version_after,
                    }
            decided = next(
                (e for e in events if e.event_kind == "swarm.interaction.decided"), None
            )
            if decided is None:
                request = InteractionPlanningRequest(
                    collet_id,
                    binding_id,
                    deepcopy(plan),
                    deepcopy(stages),
                    stage.stage_id,
                    command.session_ref,
                    command.content,
                    events[0].event_id,
                )
                snapshot = deepcopy(
                    (self._record(collet_id).collet.version, plan, stages)
                )
                decision = self.interaction_planner.decide(request)
                fresh_record, fresh_plan, fresh_stages = self._execution(
                    collet_id, binding_id
                )
                if snapshot != (fresh_record.collet.version, fresh_plan, fresh_stages):
                    raise ContextCollabVersionConflict(
                        "stale interaction planner decision"
                    )
                if not isinstance(decision, InteractionDecision):
                    raise ContextCollabValidationError(
                        "planner returned invalid decision type"
                    )
                decision.validate(stages)
                # Validate/build the complete proposed replacement BEFORE accepting a decision.
                if decision.mode is not InteractionMode.IGNORE:
                    self._revision(
                        collet_id,
                        binding_id,
                        plan,
                        stages,
                        decision,
                        events[0].event_id,
                        actor,
                        commit=False,
                    )
                decided = self._append(
                    collet_id,
                    "swarm.interaction.decided",
                    {
                        "binding_id": binding_id,
                        "request_id": command.request_id,
                        "mode": decision.mode.value,
                        "reason": decision.reason,
                        "from_stage_ref": decision.from_stage_ref,
                        "plan_ref": plan.swarm_plan_id,
                        "stage_snapshot": [s.to_dict() for s in stages],
                    },
                    actor,
                    expected_version=snapshot[0],
                )
            decision = InteractionDecision(
                InteractionMode(decided.payload["mode"]),
                decided.payload["reason"],
                decided.payload["from_stage_ref"],
            )
            result_plan = plan
            if decision.mode is not InteractionMode.IGNORE:
                if plan.swarm_plan_id == decided.payload["plan_ref"]:
                    if [s.to_dict() for s in stages] != decided.payload[
                        "stage_snapshot"
                    ]:
                        raise ContextCollabVersionConflict(
                            "accepted interaction decision has stale Stage snapshot"
                        )
                    result_plan = self._revision(
                        collet_id,
                        binding_id,
                        plan,
                        stages,
                        decision,
                        events[0].event_id,
                        actor,
                    )
                elif (
                    plan.termination_condition.get("execution_revision", {}).get(
                        "interaction_ref"
                    )
                    != events[0].event_id
                ):
                    raise ContextCollabVersionConflict(
                        "interaction decision became stale"
                    )
            result = {
                "event_ref": events[0].event_id,
                "mode": decision.mode.value,
                "reason": decision.reason,
                "plan_ref": result_plan.swarm_plan_id,
                "version": self._record(collet_id).collet.version + 1,
            }
            self._append(
                collet_id,
                "swarm.interaction.applied",
                {
                    "binding_id": binding_id,
                    "request_id": command.request_id,
                    "result": result,
                },
                actor,
            )
            return result

    def _revision(
        self,
        collet_id,
        binding_id,
        plan,
        stages,
        decision,
        event_ref,
        actor,
        *,
        commit=True,
    ):
        allocations = tuple(
            self._store.list_allocations(current_axes(), binding_id, collet_id)
        )
        revised = build_revision(
            collet_id,
            binding_id,
            plan,
            stages,
            allocations,
            decision,
            event_ref,
            self._workflow_policies.workflow_for(collet_id, binding_id),
            actor,
        )
        if commit:
            self._store.commit_swarm_revision(
                current_axes(),
                collet_id,
                binding_id,
                plan,
                stages,
                allocations,
                *revised,
            )
        return revised[-2]


__all__ = ["SwarmMethodService", "SwarmExecutionOperations"]
