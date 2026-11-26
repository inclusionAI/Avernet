"""L5 coordination: one external plan decision selects both Stage and executor.

Only collet_id is caller supplied (AssistedBuilder); infrastructure dependencies
are injected once by the composition root. No L6 import or service locator.
The bounded in-process queue is not a durable scheduler or a retry engine.
"""

from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_state import (
    current_plan,
    pending_clarifications,
    pending_interactions,
)
from copy import deepcopy
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow_ports import (
    SwarmArtifactEvidenceValidator,
)

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    CollaborationReader,
    CollaborationRecord,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.participation import (
    validate_enrolled_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmMethodOpsServiceProtocol,
    SwarmPlanOpCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.matching import (
    stage_role_candidates,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.report import (
    SwarmStageReportService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    StageExecutionService,
    SwarmRuntimeResolver,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    PlanState,
    SwarmPlan,
    Stage,
    AllocationState,
    StageState,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.swarm_planning import (
    PlanningRequest,
    PlanningDecision,
)


@dataclass(frozen=True)
class _Computed:
    decision: PlanningDecision
    # Fence decisions against Stage/Task progress between computation and commit.
    snapshot: tuple[int, SwarmPlan | None, tuple[Stage, ...]]


class SwarmDriver:
    @inject
    def __init__(
        self,
        collet_id: str,
        store: MethodStoreProtocol,
        swarm: SwarmMethodOpsServiceProtocol,
        reader: CollaborationReader,
        resolver: SwarmRuntimeResolver,
        executor: StageExecutionService,
        reports: SwarmStageReportService,
        work: ContextServiceProtocol,
        artifact_validator: SwarmArtifactEvidenceValidator,
    ) -> None:
        self.collet_id = collet_id
        self.store, self.swarm, self.reader = store, swarm, reader
        self.executor, self.reports = executor, reports
        self.work = work
        self.artifact_validator = artifact_validator
        self.axes = current_axes()
        config = resolver.resolve(collet_id)
        self.planner, self.actor = config.planner, config.actor
        self.start_actor = config.start_actor
        self.max_events = config.max_events
        bindings = [
            b
            for b in store.list_bindings(self.axes, collet_id)
            if "Swarm" in b.method_names and b.state.value == "ACTIVE"
        ]
        if len(bindings) != 1:
            raise ContextCollabValidationError(
                "driver requires exactly one ACTIVE Swarm binding"
            )
        self.binding_id = bindings[0].method_binding_id
        record = reader.get_current_status(collet_id)
        if (
            record is None
            or record.task is None
            or record.task.status not in {"created", "in_progress"}
        ):
            raise ContextCollabValidationError(
                "driver requires created or in-progress root Work"
            )
        self.task_ref = record.task.task_id
        self.queue: deque[None | _Computed] = deque()
        self.pending = False

    def plan(self) -> SwarmPlan | None:
        plans = [
            p
            for p in self.store.list_plans(self.axes, self.binding_id, self.collet_id)
            if p.task_ref == self.task_ref
        ]
        return current_plan(plans)

    def stages(self) -> list[Stage]:
        plan = self.plan()
        return [
            s
            for s in self.store.list_stages(self.axes, self.binding_id, self.collet_id)
            if plan is not None and s.swarm_plan_ref == plan.swarm_plan_id
        ]

    def _record(self) -> CollaborationRecord:
        record = self.reader.get_current_status(self.collet_id)
        if record is None:
            raise ContextCollabValidationError("Swarm Collet no longer exists")
        validate_enrolled_participation(self.collet_id, record.events, self.store)
        return record

    def is_completed(self) -> bool:
        plan = self.plan()
        stages = self.stages()
        tasks = {t.task_id: t for t in self._record().tasks}
        return (
            plan is not None
            and plan.state is PlanState.COMPLETED
            and bool(stages)
            and len({s.task_ref for s in stages}) == len(stages)
            and all(
                s.state is StageState.SUCCEEDED
                and s.output_artifact_refs
                and s.task_ref in tasks
                and tasks[s.task_ref].status == "done"
                and tasks[s.task_ref].parent_task_ref == plan.task_ref
                for s in stages
            )
            and not any(
                a.stage_ref in {s.stage_id for s in stages}
                and a.state is AllocationState.ACTIVE
                for a in self.store.list_allocations(
                    self.axes, self.binding_id, self.collet_id
                )
            )
        )

    def wake(self) -> None:
        """Start created root Work via L4/G1/G4, then enqueue Swarm planning.

        Construction never starts Work. Waking already-started Work only queues
        reconciliation; pending wakes coalesce. Start failures propagate before
        any planning is queued, and never mutate Task state directly. The runtime
        supplies a contract-selected startup identity, not the coordinator identity.
        """
        if not self.pending:
            record = self._record()
            if record.task is not None and record.task.status == "created":
                self.work.start(
                    self.collet_id,
                    WorkStartCommand(record.collet.version),
                    self.start_actor,
                )
            self.queue.append(None)
            self.pending = True

    def _snapshot(
        self,
    ) -> tuple[int, SwarmPlan | None, tuple[Stage, ...]]:
        record = self._record()
        return deepcopy(
            (
                record.collet.version,
                self.plan(),
                tuple(self.stages()),
            )
        )

    def step(self) -> bool:
        with self.store.swarm_guard(self.axes, self.collet_id):
            return self._step()

    def _step(self) -> bool:
        if not self.queue:
            return False
        event = self.queue.popleft()
        if event is None:
            self.pending = False
            plan = self.plan()
            if plan is not None and plan.state is not PlanState.RUNNING:
                return True
            if any(
                s.state in {StageState.RUNNING, StageState.ALLOCATING}
                for s in self.stages()
            ):
                return True
            record = self._record()
            if pending_interactions(record.events, self.binding_id) or any(
                pending_clarifications(record.events, self.binding_id, s.stage_id)
                for s in self.stages()
            ):
                return True
            snapshot = self._snapshot()
            decision = self.planner.plan(
                PlanningRequest(
                    self.collet_id,
                    self.task_ref,
                    record.task.objective,
                    deepcopy(tuple(self.stages())),
                    current_participation(record.events),
                    tuple(
                        {**e.payload, "event_ref": e.event_id}
                        for e in record.events
                        if (e.payload or {}).get("binding_id") == self.binding_id
                        and e.event_kind
                        in {"swarm.interaction.posted", "swarm.clarification.replied"}
                    ),
                )
            )
            self.queue.append(_Computed(decision, snapshot))
            self.pending = True
        else:
            self.pending = False
            if event.snapshot != self._snapshot():
                raise ContextCollabValidationError("stale external plan decision")
            self._apply(event.decision)
        return True

    def advance_stage(self) -> None:
        """External step boundary: reconcile/dispatch once, never drain auto-wakes."""
        with self.store.swarm_guard(self.axes, self.collet_id):
            record = self._record()
            if pending_interactions(record.events, self.binding_id) or any(
                pending_clarifications(record.events, self.binding_id, s.stage_id)
                for s in self.stages()
            ):
                return
            # Recover candidate/adoption-only failures, but never re-dispatch an
            # already accepted allocation whose external side effect is uncertain.
            plan = self.plan()
            if plan is not None and plan.state is PlanState.DRAFT:
                self.swarm.adopt_plan(
                    self.collet_id,
                    self.binding_id,
                    plan.swarm_plan_id,
                    SwarmPlanOpCommand(),
                    self.actor,
                )
                plan = self.plan()
            if plan is not None and plan.state is PlanState.READY:
                self.swarm.start_plan(
                    self.collet_id,
                    self.binding_id,
                    plan.swarm_plan_id,
                    SwarmPlanOpCommand(),
                    self.actor,
                )
            self.queue.clear()
            self.pending = False
            self.wake()
            self.step()  # Compute one decision, or observe existing in-flight work.
            if self.queue:
                self.step()  # Apply that decision only; discard synchronous auto-wake.
            self.queue.clear()
            self.pending = False

    def drain(self) -> None:
        for _ in range(self.max_events):
            if not self.step():
                return
        raise RuntimeError("Swarm driver exceeded event budget")

    def _apply(self, decision: PlanningDecision) -> None:
        if decision.finished:
            if decision.candidate is not None or decision.assignment is not None:
                raise ContextCollabValidationError("finished decision cannot dispatch")
            plan = self.plan()
            if plan is None:
                raise ContextCollabValidationError("cannot finish without a plan")
            self.swarm.complete_plan(
                self.collet_id,
                self.binding_id,
                plan.swarm_plan_id,
                SwarmPlanOpCommand(
                    "planner finished; authoritative completion checked"
                ),
                self.actor,
            )
            return
        plan = self.plan()
        if decision.assignment is None:
            raise ContextCollabValidationError(
                "plan must select a next Stage and executor"
            )
        if plan is None:
            candidate = decision.candidate
            if candidate is None or candidate.task_ref != self.task_ref:
                raise ContextCollabValidationError(
                    "initial plan must reference root Work"
                )
            # Validate both halves of the decision before granting any dispatch authority.
            if not candidate.stages or any(
                s.get("task_ref", self.task_ref) == self.task_ref
                for s in candidate.stages
            ):
                raise ContextCollabValidationError(
                    "each Stage needs independent child Work"
                )
            if len({s.get("task_ref") for s in candidate.stages}) != len(
                candidate.stages
            ):
                raise ContextCollabValidationError("Stage Tasks must be distinct")
            result = self.swarm.submit_plan_candidate(
                self.collet_id, self.binding_id, candidate, self.actor
            )
            validation = self.swarm.validate_plan(
                self.collet_id, self.binding_id, result.object_id, self.actor
            )
            if not validation["valid"]:
                raise ContextCollabValidationError(
                    f"invalid external plan: {validation['violations']}"
                )
            self.swarm.adopt_plan(
                self.collet_id,
                self.binding_id,
                result.object_id,
                SwarmPlanOpCommand(),
                self.actor,
            )
            self.swarm.start_plan(
                self.collet_id,
                self.binding_id,
                result.object_id,
                SwarmPlanOpCommand(),
                self.actor,
            )
        elif decision.candidate is not None:
            raise ContextCollabValidationError("cannot replace an existing plan")
        assignment = decision.assignment
        stage = next(
            (s for s in self.stages() if s.stage_id == assignment.stage_ref), None
        )
        record = self._record()
        if stage is None or stage.state is not StageState.READY:
            raise ContextCollabValidationError("plan assignment requires a READY Stage")
        candidates = stage_role_candidates(
            self.collet_id, stage, current_participation(record.events)
        )
        participant = next(
            (
                p
                for p in candidates
                if p.participant_id == assignment.participant_ref
                and p.agent_ref is not None
            ),
            None,
        )
        if (
            participant is None
            or not assignment.input_contract_ref
            or not assignment.output_contract_ref
        ):
            raise ContextCollabValidationError(
                "plan selected an invalid executor or I/O contract"
            )
        declared = self.swarm.declare_execution_profile(
            self.collet_id,
            self.binding_id,
            SwarmExecutionProfileCommand(
                mode="A2A_AGENT",
                target_ref=participant.participant_id,
                supported_work_kinds=["EXECUTE"],
                input_contract_ref=assignment.input_contract_ref,
                output_contract_ref=assignment.output_contract_ref,
            ),
            self.actor,
        )
        allocated = self.swarm.allocate_stage(
            self.collet_id,
            self.binding_id,
            SwarmAllocateCommand(
                stage.stage_id,
                "AGENT",
                participant.participant_id,
                declared.object_id,
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                qualification_evidence_refs=list(stage.requirement_refs),
                allocation_id=(
                    f"alloc_{stage.stage_id}_1"
                    if stage.workflow_node is not None
                    else f"alloc_{uuid4().hex}"
                ),
            ),
            self.actor,
        )
        stage = self.store.get_stage(
            self.axes, stage.stage_id, self.binding_id, self.collet_id
        )
        if stage is None:
            raise ContextCollabValidationError("allocated Stage disappeared")
        if stage.workflow_node is not None:
            self.artifact_validator.validate_inputs(stage, participant)
        self.reports.start_task(
            self.collet_id, self.binding_id, stage, participant, self.actor
        )
        profile = self.store.get_execution_profile(
            self.axes, declared.object_id, self.binding_id, self.collet_id
        )
        self.executor.execute(
            stage,
            participant,
            profile,
            allocated.object_id,
            self.collet_id,
            self.binding_id,
        )
        # A synchronous local worker may have reported already. Remote reports
        # require an explicit wake; returning from dispatch never completes Work.
        if not any(s.state is StageState.RUNNING for s in self.stages()):
            self.wake()
