"""Worker G4 reports actively reconcile Stage evidence and independent child Work.

Not a telemetry collector: actor must be the exact allocated L4 Agent. Stage
completion evidence is evaluated before any Work write. Child submission and
internal acceptance use G4's versioned Task machine; root acceptance is separate.
MethodStore is in-process; SQL + Method writes are NOT a distributed transaction.
Failures propagate, and reconciliation can resume a partially committed report.
"""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageExecutionReport,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    CollaborationReader,
    CollaborationRecord,
    AppendedEvent,
    EventWriter,
    AppendEventRecordRequest,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.work import (
    prepare_event_append,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    initial_participation,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.stage import (
    _parse_timestamp,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmMethodOpsServiceProtocol,
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    StageExecutionService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    StageState,
    Stage,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)


class SwarmStageReportService:
    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        reader: CollaborationReader,
        writer: EventWriter,
        swarm: SwarmMethodOpsServiceProtocol,
        executor: StageExecutionService,
    ) -> None:
        self.store, self.reader, self.writer = store, reader, writer
        self.swarm, self.executor = swarm, executor

    def start_task(
        self,
        collet_id: str,
        binding_id: str,
        stage: Stage,
        participant: Participant,
        actor: ActorContext,
    ) -> None:
        record = self.reader.get_current_status(collet_id)
        if record is None or participant.agent_ref is None:
            raise ContextCollabValidationError(
                "Stage requires installed Work and Agent"
            )
        parent = record.task
        if parent is None or stage.task_ref == parent.task_id:
            raise ContextCollabValidationError(
                "Stage requires an independent child Task"
            )
        if actor.actor != parent.principal_ref:
            raise ContextCollabValidationError(
                "child Work must be started by its principal"
            )
        allocation = next(
            (
                a
                for a in self.store.list_allocations(
                    current_axes(), binding_id, collet_id
                )
                if a.stage_ref == stage.stage_id
                and a.state is AllocationState.ACTIVE
                and a.target_ref == participant.participant_id
            ),
            None,
        )
        if allocation is None:
            raise ContextCollabValidationError("child Work requires allocated Agent")
        if any(t.task_id == stage.task_ref for t in record.tasks):
            raise ContextCollabValidationError("Stage Task already exists")
        creation_ref = f"evt_{uuid4().hex}"
        task = Task(
            stage.task_ref,
            collet_id,
            stage.name,
            "created",
            1,
            parent.principal_ref,
            (participant.agent_ref,),
            parent.task_id,
            tuple(stage.output_artifact_roles),
            creation_ref,
            creation_ref,
        )
        self._work(record, task, "task.started", {}, actor, creation=task)

    def _work(
        self,
        record: CollaborationRecord,
        task: Task,
        event: str,
        payload: dict,
        actor: ActorContext,
        *,
        creation: Task | None = None,
    ) -> AppendedEvent:
        prepared = prepare_event_append(
            record.collet.collet_id,
            AppendEventRecordRequest(
                record.collet.version,
                event,
                "Task",
                actor.actor,
                record.events[-1].event_id,
                "public",
                payload,
            ),
            task=task,
            actor=actor.actor,
            workflow_kind="task_commission_goal",
            workflow_version=1,
            require_transition=True,
        )
        if creation is not None:
            prepared = replace(
                prepared, request=replace(prepared.request, task_creation=creation)
            )
        return prepared.commit(self.writer)

    def report(self, report: StageExecutionReport, actor: ActorContext) -> None:
        if (
            not isinstance(report.artifact_refs, tuple)
            or not report.artifact_refs
            or any(
                not isinstance(ref, str) or not ref.strip()
                for ref in report.artifact_refs
            )
            or len(set(report.artifact_refs)) != len(report.artifact_refs)
        ):
            raise ContextCollabValidationError(
                "report requires nonempty distinct artifact refs"
            )
        axes = current_axes()
        stage = self.store.get_stage(
            axes, report.stage_ref, report.binding_id, report.collet_id
        )
        allocation = self.store.get_allocation(
            axes, report.allocation_ref, report.binding_id, report.collet_id
        )
        binding = self.store.get_binding(axes, report.binding_id, report.collet_id)
        record = self.reader.get_current_status(report.collet_id)
        if (
            stage is None
            or allocation is None
            or record is None
            or binding is None
            or binding.state.value != "ACTIVE"
            or "Swarm" not in binding.method_names
            or allocation.stage_ref != stage.stage_id
            or stage.state is not StageState.RUNNING
            or allocation.state is not AllocationState.ACTIVE
            or _parse_timestamp(
                allocation.lease_expires_at, "allocation", "lease_expires_at"
            )
            <= datetime.now(timezone.utc)
        ):
            raise ContextCollabValidationError(
                "report requires a live Stage allocation"
            )
        participant = next(
            (
                p
                for p in initial_participation(record.events).participants
                if p.participant_id == allocation.target_ref
            ),
            None,
        )
        if (
            participant is None
            or participant.agent_ref is None
            or (participant.agent_ref.agent_type, participant.agent_ref.agent_id)
            != (actor.actor.principal_type, actor.actor.principal_id)
        ):
            raise ContextCollabValidationError(
                "report actor is not the allocated Agent"
            )
        task = next((t for t in record.tasks if t.task_id == stage.task_ref), None)
        plan = self.store.get_plan(
            axes, stage.swarm_plan_ref, report.binding_id, report.collet_id
        )
        if (
            task is None
            or plan is None
            or task.parent_task_ref != plan.task_ref
            or participant.agent_ref not in task.executor_refs
            or not stage.completion_condition
            or not report.artifact_refs
            or not self.executor.accepts(stage, report)
        ):
            raise ContextCollabValidationError(
                "unacceptable Stage report or child Task"
            )
        # A retry may resume SQL progress, but cannot replace previously submitted
        # evidence after a partial commit. Keep Stage evidence tied to Work history.
        if task.status in {"reviewing", "done"}:
            previous = next(
                (e for e in record.events if e.event_id == task.last_event_ref), None
            )
            expected = (
                "task.submitted" if task.status == "reviewing" else "task.accepted"
            )
            if (
                previous is None
                or previous.task_ref != task.task_id
                or previous.event_kind != expected
                or previous.payload.get("artifacts") != list(report.artifact_refs)
            ):
                raise ContextCollabValidationError(
                    "completion report changed committed Task evidence"
                )
        # Internal Stage acceptance is accountable to the root principal. It does
        # not authorize product/root acceptance or impersonate the worker report.
        owner = ActorContext(task.principal_ref, source="swarm_stage_acceptance")
        if task.status == "in_progress":
            self._work(
                record,
                task,
                "task.submitted",
                {"artifacts": list(report.artifact_refs)},
                actor,
            )
            record = self.reader.get_current_status(report.collet_id)
            task = next(t for t in record.tasks if t.task_id == stage.task_ref)
        if task.status == "reviewing":
            self._work(
                record,
                task,
                "task.accepted",
                {"artifacts": list(report.artifact_refs)},
                owner,
            )
        elif task.status != "done":
            raise ContextCollabValidationError(
                "Stage Task cannot accept a completion report"
            )
        self.swarm.complete_stage(
            report.collet_id,
            report.binding_id,
            report.stage_ref,
            SwarmStageOutcomeCommand(
                [report.allocation_ref],
                "worker G4 completion report",
                report.artifact_refs,
            ),
            owner,
        )
