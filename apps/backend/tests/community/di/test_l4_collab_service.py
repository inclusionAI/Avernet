"""The complete L4 Service API resolves directly, preserving storage/G1 policy."""

from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)

from inspect import signature
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import Mock

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    ColletWriter,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol as L4ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabServiceProtocol as ApplicationCollabServiceProtocol,
)
from agentclaw.community.di.modules.task_module import TaskModule
from injector import Injector, InstanceProvider

ACTOR = ActorContext(PrincipalRef("human", "owner"), "api")
SPACE = SpaceRef("space", "test")
WORKERS = (AgentRef("bot", "worker-1"), AgentRef("bot", "worker-2"))


@pytest.fixture
def creation_runtime():
    writer = Mock(spec=ColletWriter)
    writer.create_l4_collet.return_value = SimpleNamespace(
        collet=Collet("collet-test", ACTOR.actor, ColletState.ACTIVE, SPACE, 1)
    )
    authorizer = Mock(spec=ContextCollabAuthorizerProtocol)

    def reject_upper_resolution():
        raise AssertionError("L4 creation must not resolve application composition")

    def creation_ports(binder):
        binder.bind(
            L4ContextCollabRepositoryProtocol[ContextStatusRecord],
            to=InstanceProvider(writer),
        )
        binder.bind(ContextCollabAuthorizerProtocol, to=authorizer)
        binder.bind(ProductProfileReader, to=ProductCatalog(()))
        for contract in (
            ContextCollabRepositoryProtocol,
            ApplicationCollabServiceProtocol,
            MethodStoreProtocol,
        ):
            binder.bind(contract, to=reject_upper_resolution)

    # The real TaskModule owns the public binding; only dependencies are faked.
    injector = Injector([TaskModule(), creation_ports], auto_bind=False)
    return injector, writer, authorizer


def test_l4_protocol_conforms_to_full_implementation():
    assert ContextService.__name__ == "ContextService"
    assert ContextServiceProtocol.__name__ == "ContextServiceProtocol"
    assert issubclass(ContextService, ContextServiceProtocol)
    methods = {
        "create_collet",
        "create_task",
        "start",
        "append_event",
        "get_status",
        "get_events",
    }
    assert {
        name
        for name, value in vars(ContextServiceProtocol).items()
        if not name.startswith("_") and callable(value)
    } == methods
    assert {
        name
        for name, value in vars(ContextService).items()
        if not name.startswith("_") and callable(value)
    } == methods
    for name in methods:
        actual, contract = (
            getattr(ContextService, name),
            getattr(ContextServiceProtocol, name),
        )
        assert signature(actual) == signature(contract)
        assert get_type_hints(actual) == get_type_hints(contract)
    assert ContextService.__module__.endswith("l4_context_primitives.context_service")
    assert ContextServiceProtocol.__module__.endswith(
        "l4_context_primitives.context_service_protocol"
    )


def test_l6_contract_only_extends_l4_and_preserves_compatibility_type_identity():
    from agentclaw.community.core.task.context_collab import (
        service_api as application_api,
    )
    from agentclaw.community.core.task.context_collab.l4_context_primitives import (
        context_service_protocol as l4_api,
    )
    from agentclaw.community.core.task.context_collab.l6_products import (
        service_api as l6_api,
    )
    from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
        ContextCollabService as ApplicationCollabService,
    )

    assert (
        application_api.ContextCollabServiceProtocol
        is l6_api.ContextCollabServiceProtocol
    )
    assert (
        application_api.CollaborationCreateCommand is l6_api.CollaborationCreateCommand
    )
    assert l6_api.ContextCollabServiceProtocol.__module__.endswith(
        "l6_products.service_api"
    )
    assert ApplicationCollabService.__module__.endswith(
        "l6_products.operations.collaboration_creation"
    )
    assert application_api.ContextCollabServiceProtocol is not ContextServiceProtocol
    assert issubclass(
        application_api.ContextCollabServiceProtocol, ContextServiceProtocol
    )
    for name in (
        "ColletCreateCommand",
        "TaskCreateCommand",
        "WorkStartCommand",
        "EventAppendCommand",
        "EventAcceptedResult",
        "ContextCollabStatusProjection",
        "TaskProgressProjectionDTO",
    ):
        assert getattr(application_api, name) is getattr(l4_api, name)
    for name in (
        "create_collet",
        "create_task",
        "start",
        "append_event",
        "get_status",
        "get_events",
    ):
        assert getattr(ApplicationCollabService, name) is getattr(ContextService, name)
    assert not hasattr(ContextService, "create_collaboration")


def test_l4_creation_resolves_direct_singleton_without_application_facade(
    creation_runtime,
):
    injector, writer, authorizer = creation_runtime
    service = injector.get(ContextServiceProtocol)
    assert type(service) is ContextService
    assert isinstance(service, ContextServiceProtocol)
    assert service is injector.get(ContextServiceProtocol)
    assert service._repository is writer
    assert service._authorizer is authorizer


def test_injected_creation_is_unassigned_and_uses_selected_g1_policy(creation_runtime):
    injector, writer, authorizer = creation_runtime
    command = ColletCreateCommand(ACTOR.actor)
    created = injector.get(ContextServiceProtocol).create_collet(command, ACTOR)
    assert created is writer.create_l4_collet.return_value.collet
    authorizer.check_create_collet.assert_called_once_with(command=command, actor=ACTOR)
    writer.create_l4_collet.assert_called_once_with(command, ACTOR.actor)
    assert not hasattr(command, "executor_refs")


def test_injected_creation_rejection_never_calls_writer(creation_runtime):
    injector, writer, authorizer = creation_runtime
    authorizer.check_create_collet.side_effect = ContextCollabValidationError(
        "deployment creation denied"
    )
    command = ColletCreateCommand(ACTOR.actor)
    with pytest.raises(
        ContextCollabValidationError, match="deployment creation denied"
    ):
        injector.get(ContextServiceProtocol).create_collet(command, ACTOR)
    authorizer.check_create_collet.assert_called_once_with(command=command, actor=ACTOR)
    writer.create_l4_collet.assert_not_called()


def test_injected_creation_propagates_storage_failure(creation_runtime):
    injector, writer, authorizer = creation_runtime
    writer.create_l4_collet.side_effect = RuntimeError("storage write failed")
    command = ColletCreateCommand(ACTOR.actor)
    with pytest.raises(RuntimeError, match="storage write failed"):
        injector.get(ContextServiceProtocol).create_collet(command, ACTOR)
    authorizer.check_create_collet.assert_called_once_with(command=command, actor=ACTOR)
    writer.create_l4_collet.assert_called_once_with(command, ACTOR.actor)
