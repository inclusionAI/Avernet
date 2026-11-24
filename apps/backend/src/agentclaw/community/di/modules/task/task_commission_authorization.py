"""Install commission's admission prerequisite at the existing G1 seam.

Both generic L4 writes and product Work calls use this seam; a designated role
is not proof of Open admission. Existing products/v1 retain structural behavior.
"""

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_profile_ref,
    creation_product_kind,
    creation_workflow_kind,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    OpenMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol as L4ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    BotSelfApplicationAdmission,
    OpenAdmissionPolicy,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskMethodOpsPort,
)


class TaskMethodOpsAuthorizer(StructuralMethodOpsAuthorizer):
    """Authorize Open's configured admission Actor at the G1 method seam.

    A pinned product policy replaces the historical owner/executor face for
    ``screen``/``admit`` when it selects a configured decision Agent. The
    caller must exactly match that AgentRef; it is a policy decision identity,
    not an initial task executor or role binding. All other operations, and
    products without a configured Agent, retain structural owner/executor
    behavior.
    """

    @inject
    def __init__(
        self,
        catalog: ProductProfileReader,
        repository: L4ContextCollabRepositoryProtocol[ContextStatusRecord],
    ) -> None:
        self._catalog = catalog
        self._repository = repository

    def check_open_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        application=None,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None:
        if op in {"screen", "admit"}:
            admission = self._configured_admission_state(binding)
            if admission is not None:
                self._authorize_configured_admission_agent(admission, actor)
                return
        super().check_open_op(
            op,
            binding,
            actor,
            application=application,
            principal_id=principal_id,
            executor_id=executor_id,
        )

    def _configured_admission_state(
        self, binding: MethodBinding
    ) -> OpenAdmissionPolicy | None:
        record = self._repository.get_current_status(binding.collab_context_id)
        if record is None:
            return None
        profile = product_definition(
            product_kind=creation_product_kind(record.creation_facts),
            workflow_kind=creation_workflow_kind(record.creation_facts),
            method_kind=creation_method_kind(record.creation_facts),
            profile_ref=creation_profile_ref(record.creation_facts),
            catalog=self._catalog,
        )
        if profile is None:
            return None
        policy = profile.composition.require_policy("Open")
        admission = policy.publication.admission
        if not isinstance(admission, OpenAdmissionPolicy):
            return None
        if admission.decision_actor != "configured_agent" or not isinstance(
            admission.decision_agent_ref, AgentRef
        ):
            return None
        return admission

    def _authorize_configured_admission_agent(
        self,
        admission: OpenAdmissionPolicy,
        actor: ActorContext,
    ) -> None:
        configured_agent = admission.decision_agent_ref
        if not isinstance(configured_agent, AgentRef):
            raise ContextCollabValidationError(
                "admission policy has no configured decision Agent"
            )
        if configured_agent != AgentRef(
            actor.actor.principal_type, actor.actor.principal_id
        ):
            raise ContextCollabValidationError(
                "actor is not the configured admission decision Agent"
            )


class CommissionAdmissionAuthorizer(StructuralContextCollabAuthorizer):
    @inject
    def __init__(
        self, catalog: ProductProfileReader, method_ops: TaskMethodOpsPort
    ) -> None:
        self._catalog = catalog
        self._method_ops = method_ops

    def check_append(
        self,
        collet: Collet,
        task: Task | None,
        creation_facts: dict,
        product_instance: ProductInstance | None,
        command,
        actor: ActorContext,
    ) -> None:
        super().check_append(
            collet, task, creation_facts, product_instance, command, actor
        )
        profile = product_definition(
            product_kind=creation_product_kind(creation_facts),
            workflow_kind=creation_workflow_kind(creation_facts),
            method_kind=creation_method_kind(creation_facts),
            profile_ref=creation_profile_ref(creation_facts),
            catalog=self._catalog,
        )
        if (
            profile is not None
            and any(
                isinstance(method.policy, OpenMethodPolicy)
                and isinstance(
                    method.policy.publication.admission, BotSelfApplicationAdmission
                )
                for method in profile.composition.required_methods
            )
            and command.event_kind
            in {"task.started", "task.progress.updated", "task.submitted"}
        ):
            self._method_ops.require_admitted(collet.collet_id, actor)
