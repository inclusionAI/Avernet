"""L6 composition owns product implementations, preserving legacy import identity."""

from inspect import signature
from typing import get_type_hints

from agentclaw.community.core.task.context_collab.l5_methods.operations import (
    lifecycle as l5_lifecycle,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    collaboration_policy,
    method_bootstrap,
    method_lifecycle,
    product_instantiation,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.services import (
    method_bootstrap as legacy_bootstrap,
)
from agentclaw.community.core.task.context_collab.services import (
    method_lifecycle_service as legacy_lifecycle,
)


def test_product_instantiation_implements_its_l6_contract():
    service = product_instantiation.ProductInstantiationService
    actual = service.instantiate
    contract = ProductInstantiationServiceProtocol.instantiate
    assert service.__module__.endswith("l6_products.operations.product_instantiation")
    assert get_type_hints(actual) == get_type_hints(contract)
    assert [
        (p.name, p.kind, p.default) for p in signature(actual).parameters.values()
    ] == [(p.name, p.kind, p.default) for p in signature(contract).parameters.values()]


def test_product_policy_and_recovery_implementations_are_l6_owned():
    assert collaboration_policy.ProductCollaborationPolicy.__module__.endswith(
        "l6_products.operations.collaboration_policy"
    )
    assert method_bootstrap.ProductMethodBindingRecovery.__module__.endswith(
        "l6_products.operations.method_bootstrap"
    )
    assert method_lifecycle.MethodLifecycleService.__module__.endswith(
        "l6_products.operations.method_lifecycle"
    )


def test_legacy_bootstrap_imports_are_identity_aliases_not_wrappers():
    for name in (
        "ProductMethodBindingRecovery",
        "_resolve_method_spec",
        "project_created_context",
        "ensure_top_level_binding",
    ):
        assert getattr(legacy_bootstrap, name) is getattr(method_bootstrap, name)
    for name in ("project_created_context", "ensure_top_level_binding"):
        assert getattr(legacy_lifecycle, name) is getattr(method_bootstrap, name)
    assert (
        legacy_lifecycle.MethodLifecycleService
        is method_lifecycle.MethodLifecycleService
    )


def test_l6_legacy_constructor_inherits_l5_operations_without_reimplementation():
    for name, value in vars(l5_lifecycle.MethodLifecycleService).items():
        if not name.startswith("_") and callable(value):
            assert getattr(method_lifecycle.MethodLifecycleService, name) is value


def test_persistence_compatibility_paths_preserve_canonical_type_identity():
    from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence import (
        models as l4_models,
    )
    from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence import (
        task_storage as l4_tasks,
    )
    from agentclaw.community.core.task.context_collab.l6_products.persistence import (
        repository as l6_repository,
    )
    from agentclaw.community.core.task.context_collab.persistence import (
        models,
        repository,
        task_storage,
    )

    assert l6_repository.ContextCollabRepository.__module__.endswith(
        "l6_products.persistence.repository"
    )
    for name in (
        "ContextCollabRepository",
        "ContextCollabRepositoryProtocol",
        "AppendEventRecordRequest",
        "CreateColletRecordRequest",
        "InitialEventDraft",
        "ContextCreatedRecord",
        "ContextStatusRecord",
        "EventAppendedRecord",
    ):
        assert getattr(repository, name) is getattr(l6_repository, name)
    for name in (
        "ContextCollabColletModel",
        "ContextCollabTaskModel",
        "ContextCollabEventModel",
    ):
        actual = getattr(l4_models, name)
        assert actual.__module__.endswith("l4_context_primitives.persistence.models")
        assert getattr(models, name) is actual
        assert getattr(models, name).__table__ is actual.__table__
    for name in ("load_tasks", "legacy_task", "legacy_progress"):
        assert getattr(task_storage, name) is getattr(l4_tasks, name)
