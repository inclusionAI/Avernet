"""L6-owned creation of product collaborations and existing-Collet binding.

L6 orchestrates the public L4 creation port before selecting the product. L4
creation, SQL selection and MethodStore writes are separate transactions; failures
after creation expose the committed Collet for recovery, never a silent retry.
No recovery may replace the immutable product selection.
"""

from uuid import uuid4

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ProductInstantiationIncompleteError,
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
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    prepare_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    composition_method_kind,
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
    ProductInstantiateNewCommand,
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


class ProductInstantiationService(ProductInstantiationServiceProtocol):
    @inject
    def __init__(
        self,
        repository: ContextCollabRepositoryProtocol,
        method_store: MethodStoreProtocol,
        product_catalog: ProductCatalog,
        authorizer: ContextCollabAuthorizerProtocol,
        context_service: ContextServiceProtocol,
    ) -> None:
        self._repository = repository
        self._store = method_store
        self._catalog = product_catalog
        self._authorizer = authorizer
        self._context_service = context_service

    def instantiate(
        self,
        command: ProductInstantiateNewCommand | ProductInstantiateCommand,
        context: ActorContext,
    ) -> ProductInstance:
        if isinstance(command, ProductInstantiateNewCommand):
            return self._instantiate_new(command, context)
        return self._instantiate_existing(command, context)

    def _instantiate_new(
        self, command: ProductInstantiateNewCommand, context: ActorContext
    ) -> ProductInstance:
        # Fail invalid product inputs before L4 commits a context. L4 remains
        # responsible for creation authorization, identity and persistence.
        if context.actor != command.collet.initiator_ref:
            raise ContextCollabValidationError(
                "only the Collet initiator may instantiate a new product collaboration"
            )
        profile = self._catalog.get(
            command.profile_ref.profile_id, command.profile_ref.version
        )
        executor_ref = command.executor_ref
        if executor_ref is None and len(command.participant_refs) == 1:
            executor_ref = command.participant_refs[0]
        product_participation(
            profile,
            command.collet.initiator_ref,
            command.participant_refs,
            executor_ref,
        )
        prepare_product_profile(
            product_kind=profile.ref.profile_id,
            workflow_kind=profile.workflow.ref.profile_id if profile.workflow else None,
            method_kind=composition_method_kind(profile.composition),
            profile_ref=profile.ref,
            principal_ref=command.collet.initiator_ref,
            executor_ref=executor_ref,
            catalog=self._catalog,
        )
        self._authorizer.check_create(
            method_kind=composition_method_kind(profile.composition),
            executor_ref=executor_ref,
            actor=context,
        )
        collet = self._context_service.create_collet(command.collet, context)
        try:
            return self._instantiate_existing(
                ProductInstantiateCommand(
                    collet_id=collet.collet_id,
                    profile_ref=command.profile_ref,
                    expected_version=collet.version,
                    participant_refs=command.participant_refs,
                    executor_ref=command.executor_ref,
                    resource_refs=command.resource_refs,
                ),
                context,
            )
        except Exception as error:
            # SQL and MethodStore are separate authorities. Preserve the actual
            # cause and the committed identity so callers can recover in place.
            raise ProductInstantiationIncompleteError(
                collet.collet_id, collet.version
            ) from error

    def _instantiate_existing(
        self, command: ProductInstantiateCommand, context: ActorContext
    ) -> ProductInstance:
        record = self._repository.get_current_status(command.collet_id)
        if record is None:
            raise ContextCollabNotFoundError(f"context {command.collet_id!r} not found")
        task = record.task
        principal = (
            task.principal_ref if task is not None else record.collet.initiator_ref
        )
        if context.actor not in {
            record.collet.initiator_ref,
            principal,
        }:
            raise ContextCollabValidationError(
                "only the Collet initiator or responsible principal may instantiate"
            )
        ensure_expected_version(command.expected_version, record.collet.version)
        untouched = (
            not record.events
            if task is None
            else len(record.events) == 1
            and record.events[0].event_id == task.created_event_ref
            and record.events[0].context_version_after == record.collet.version
            and task.version == 1
        )
        if record.product_instance is not None or not untouched:
            raise ContextCollabValidationError(
                "product instantiation requires an untouched L4 Collet"
            )
        event_id = task.created_event_ref if task is not None else "evt_" + uuid4().hex
        profile = self._catalog.get(
            command.profile_ref.profile_id, command.profile_ref.version
        )
        executor_ref = command.executor_ref
        if executor_ref is None and len(command.participant_refs) == 1:
            executor_ref = command.participant_refs[0]
        participation = product_participation(
            profile, principal, command.participant_refs, executor_ref
        )
        self._authorizer.check_create(
            method_kind=composition_method_kind(profile.composition),
            executor_ref=executor_ref,
            actor=context,
        )
        facts = creation_payload(
            method_kind=composition_method_kind(profile.composition),
            executor_ref=executor_ref,
            objective=task.objective if task is not None else None,
            resource_refs=command.resource_refs,
            product_kind=profile.ref.profile_id,
            workflow_kind=profile.workflow.ref.profile_id if profile.workflow else None,
            product_profile_ref=profile.ref,
        )
        instance = instantiate_product(
            facts=facts,
            collet_id=command.collet_id,
            root_task_id=task.task_id if task is not None else None,
            event_id=event_id,
            principal_ref=principal,
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
            method_kind=composition_method_kind(profile.composition),
            principal_ref=principal,
            executor_ref=executor_ref,
            profile=profile,
            context=context,
            now=_now(),
        )
        return instance
