"""L6-owned explicit product instantiation on existing L4 work.

L4 creation is already committed. SQL selection and the in-memory MethodStore
are not a shared transaction; errors propagate and binding recovery can repair
an installed selection. No retry may replace that immutable product selection.
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
from agentclaw.community.core.task.context_collab.g2_keep_reliable_live.reliability import (
    ensure_expected_version,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_payload,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
    instantiate_product,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_bootstrap import (
    project_created_context,
)
from agentclaw.community.core.task.context_collab.l6_products.participation import (
    product_participation,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from injector import inject


class ProductInstantiationService(ProductInstantiationServiceProtocol):
    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        method_store: MethodStoreProtocol,
        product_catalog: ProductCatalog,
        authorizer: ContextCollabAuthorizerProtocol,
    ) -> None:
        self._repository = repository
        self._store = method_store
        self._catalog = product_catalog
        self._authorizer = authorizer

    def instantiate(
        self, command: ProductInstantiateCommand, context: ActorContext
    ) -> ProductInstance:
        record = self._repository.get_current_status(command.collet_id)
        if record is None:
            raise ContextCollabNotFoundError(f"context {command.collet_id!r} not found")
        task = record.task
        if task is None or context.actor not in {
            record.collet.initiator_ref,
            task.principal_ref,
        }:
            raise ContextCollabValidationError(
                "only the Collet initiator or responsible principal may instantiate"
            )
        ensure_expected_version(command.expected_version, record.collet.version)
        if (
            record.product_instance is not None
            or record.collet.version != 1
            or task.version != 1
        ):
            raise ContextCollabValidationError(
                "product instantiation requires an untouched L4 Collet"
            )
        if task.created_event_ref is None:
            raise ContextCollabValidationError(
                "product instantiation requires creation evidence"
            )
        profile = self._catalog.get(
            command.profile_ref.profile_id, command.profile_ref.version
        )
        executor_ref = command.executor_ref
        if executor_ref is None and len(command.participant_refs) == 1:
            executor_ref = command.participant_refs[0]
        participation = product_participation(
            profile, task.principal_ref, command.participant_refs, executor_ref
        )
        self._authorizer.check_create(
            method_kind=profile.composition.method_kind,
            executor_ref=executor_ref,
            actor=context,
        )
        facts = creation_payload(
            method_kind=profile.composition.method_kind,
            executor_ref=executor_ref,
            objective=task.objective,
            resource_refs=(),
            product_kind=profile.ref.profile_id,
            workflow_kind=profile.workflow.ref.profile_id if profile.workflow else None,
            product_profile_ref=profile.ref,
        )
        instance = instantiate_product(
            facts=facts,
            collet_id=command.collet_id,
            root_task_id=task.task_id,
            event_id=task.created_event_ref,
            principal_ref=task.principal_ref,
            executor_ref=executor_ref,
            catalog=self._catalog,
        )
        if instance is None:
            raise ContextCollabValidationError(
                "product instantiation requires a pinned profile"
            )
        self._repository.attach_product_instance(
            command.collet_id,
            expected_version=command.expected_version,
            facts=facts,
            instance=instance,
            participation=participation,
            actor_ref=context.actor,
        )
        project_created_context(
            self._store,
            current_axes(),
            command.collet_id,
            method_kind=profile.composition.method_kind,
            principal_ref=task.principal_ref,
            executor_ref=executor_ref,
            profile=profile,
            context=context,
            now=_now(),
        )
        return instance
