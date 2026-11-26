"""G1 access for local Task/Collet self-reports; no fake Work append.

Telemetry is conservatively workspace-scoped: principal/designated executors
only, with current Task evidence visible through the existing G1 event filter.
Public Event visibility alone does not grant telemetry access. This is not a
Resource grant, remote observer delegation, or an execution/admission command.
"""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.telemetry import (
    TelemetryContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    ObjectRef,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    initial_executor_refs,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)


def _local_ref(ref: ObjectRef, kind: str) -> None:
    if not isinstance(ref, ObjectRef):
        raise ContextCollabValidationError(f"expected local {kind} reference")
    ObjectRef.from_dict(ref.to_dict())
    if ref.object_type != kind or ref.issuer not in (None, "avernet"):
        raise ContextCollabValidationError(f"expected local {kind} reference")
    if len(ref.object_id) > 128:
        raise ContextCollabValidationError(f"{kind} id exceeds 128 characters")


class TelemetryAccess:
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        authorizer: ContextCollabAuthorizerProtocol,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer

    def _require_scope(self, collet_ref, subject_ref, actor):
        _local_ref(collet_ref, "Collet")
        if not isinstance(subject_ref, ObjectRef) or subject_ref.object_type not in {
            "Task",
            "Collet",
        }:
            raise ContextCollabValidationError(
                "local telemetry supports Task/Collet subjects only"
            )
        _local_ref(subject_ref, subject_ref.object_type)
        state = self._repository.get_current_status(collet_ref.object_id)
        if state is None:
            raise ContextCollabNotFoundError("telemetry context not found")
        task = state.task
        if subject_ref.object_type == "Task":
            task = next(
                (t for t in state.tasks if t.task_id == subject_ref.object_id), None
            )
            if task is None:
                raise ContextCollabValidationError(
                    "telemetry Task is not in this Collet"
                )
        elif subject_ref.object_id != state.collet.collet_id:
            raise ContextCollabValidationError(
                "telemetry Collet does not match its context"
            )
        agent = AgentRef(actor.actor.principal_type, actor.actor.principal_id)
        executors = task.executor_refs if task else initial_executor_refs(state.events)
        principal = task.principal_ref if task else state.collet.initiator_ref
        if actor.actor != principal and agent not in executors:
            raise ContextCollabValidationError(
                "telemetry access requires principal or executor"
            )
        if state.product_instance is not None and actor.actor != principal:
            state.product_instance.participant_scope.require(agent)
        visible = self._authorizer.filter_visible_events(
            state.collet,
            task,
            state.creation_facts,
            state.product_instance,
            state.events,
            actor,
        )
        if (
            task is not None
            and task.last_event_ref is not None
            and task.last_event_ref not in {event.event_id for event in visible}
        ):
            raise ContextCollabValidationError(
                "telemetry subject evidence is not visible"
            )
        return state

    def check_query(
        self, collet_ref: ObjectRef, subject_ref: ObjectRef, actor: ActorContext
    ) -> None:
        self._require_scope(collet_ref, subject_ref, actor)

    def check_record(
        self, context: TelemetryContext, subject_ref: ObjectRef, actor: ActorContext
    ) -> None:
        if context.collet_ref is None:
            raise ContextCollabValidationError(
                "local telemetry requires context.collet_ref"
            )
        state = self._require_scope(context.collet_ref, subject_ref, actor)
        if subject_ref.object_type == "Task" and context.task_ref != subject_ref:
            raise ContextCollabValidationError(
                "context.task_ref must match the Task subject"
            )
        if context.task_ref is not None:
            _local_ref(context.task_ref, "Task")
            if not any(t.task_id == context.task_ref.object_id for t in state.tasks):
                raise ContextCollabValidationError("context Task is not in this Collet")
        principal = actor.accountable_principal_ref
        if actor.actor.principal_type != "bot":
            principal = actor.actor
        expected_refs = {
            "agent_ref": ("Agent", actor.actor.principal_id)
            if actor.actor.principal_type == "bot"
            else None,
            "principal_ref": ("Principal", principal.principal_id)
            if principal
            else None,
            "space_ref": ("Space", state.collet.scope.space_id)
            if state.collet.scope
            else None,
        }
        for name, expected in expected_refs.items():
            ref = getattr(context, name)
            if ref is not None:
                if expected is None:
                    raise ContextCollabValidationError(f"{name} is not applicable")
                _local_ref(ref, expected[0])
                if ref.object_id != expected[1]:
                    raise ContextCollabValidationError(
                        f"{name} does not match telemetry scope/actor"
                    )
        if context.activity_ref is not None:
            raise ContextCollabValidationError(
                "local telemetry acquisition does not support Activity refs"
            )
        instance = state.product_instance
        if context.method_binding_ref is not None:
            _local_ref(context.method_binding_ref, "MethodBinding")
            if (
                instance is None
                or context.method_binding_ref.object_id
                not in instance.method_binding_refs
            ):
                raise ContextCollabValidationError(
                    "MethodBinding is not in this product instance"
                )
        if context.product_profile_ref is not None:
            ref = context.product_profile_ref
            _local_ref(ref, "ProductProfile")
            if instance is None or (ref.object_id, ref.version) != (
                instance.profile_ref.profile_id,
                str(instance.profile_ref.version),
            ):
                raise ContextCollabValidationError(
                    "ProductProfile does not match the pinned instance"
                )
