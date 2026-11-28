"""Independent Method bindings, explicit composition and pinned topology recovery."""

from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)

from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionEdgeState,
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    GroupMethodOpsServiceProtocol,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.lifecycle import (
    MethodLifecycleService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodRelationTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_bootstrap import (
    ProductMethodBindingRecovery,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionCreatedView,
    TaskMethodOpsPort,
)

from tests.community.repository.task.context_collab.context_spec_test_support import (
    store_context_value,
    stored_context_value,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    HUMAN,
    OBJECTIVE,
    _create_commission_collet,
    _create_product_profile,
    _select_admitted_agent,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    commission_deployment as _commission_deployment_fixture,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    deploy_commission as _deploy_commission_fixture,
)
from tests.community.core.task.context_collab.products.test_task_commission_open_single import (
    admitted_runtime as _admitted_runtime_fixture,
)
from tests.community.core.task.context_collab.products.test_task_commission_open_single import (
    instance_runtime as _instance_runtime_fixture,
)
from tests.community.factories.task_commission import BOT_ID as BOT_ASSIGNEE

admitted_runtime = _admitted_runtime_fixture
instance_runtime = _instance_runtime_fixture
commission_deployment = _commission_deployment_fixture
deploy_commission = _deploy_commission_fixture

NOW = "2026-11-17T00:00:00+00:00"


def _recovery(injector, store):
    return ProductMethodBindingRecovery(
        store,
        injector.get(ContextCollabRepositoryProtocol),
        injector.get(ProductProfileReader),
    )


def _inspect_with_store(injector, store, collet_id, binding_id):
    return MethodLifecycleService(
        store,
        injector.get(MethodOpsAuthorizerProtocol),
        injector.get(ContextCollabRepositoryProtocol),
        recovery=_recovery(injector, store),
    ).inspect(collet_id, binding_id, HUMAN)


@pytest.mark.parametrize("method_index", [0, 1])
def test_restart_inspection_recovers_both_profiles_and_composition(
    instance_runtime, method_index
):
    _, _, injector, collet, instance = instance_runtime
    fresh_store, axes = InMemoryMethodStore(), current_axes()
    binding_id = instance.method_binding_refs[method_index]
    _inspect_with_store(injector, fresh_store, collet.collet_id, binding_id)
    assert {
        item.method_binding_id
        for item in fresh_store.list_bindings(axes, collet.collet_id)
    } == set(instance.method_binding_refs)
    assert {
        tuple(item.method_names)
        for item in fresh_store.list_bindings(axes, collet.collet_id)
    } == {("Open",), ("Single",)}
    [edge] = fresh_store.list_composition_edges(axes, collet.collet_id)
    assert (edge.parent_binding, edge.child_binding) == tuple(
        reversed(instance.method_binding_refs)
    )
    before = [
        item.to_dict() for item in fresh_store.list_bindings(axes, collet.collet_id)
    ]
    _recovery(injector, fresh_store).recover(
        collet.collet_id, HUMAN, method_binding_id=binding_id, now=NOW
    )
    assert [
        item.to_dict() for item in fresh_store.list_bindings(axes, collet.collet_id)
    ] == before
    assert fresh_store.list_composition_edges(axes, collet.collet_id) == [edge]
    assert (
        injector.get(ContextCollabRepositoryProtocol)
        .get_current_status(collet.collet_id)
        .product_instance
        == instance
    )


@pytest.mark.parametrize("unknown", ["arbitrary", "legacy_composite"])
def test_unknown_binding_inspection_does_not_write_any_projection(
    instance_runtime, unknown
):
    _, _, injector, collet, _ = instance_runtime
    store, axes = InMemoryMethodStore(), current_axes()
    binding_id = (
        "mb_unknown"
        if unknown == "arbitrary"
        else top_level_binding_id(collet.collet_id)
    )
    with pytest.raises(ContextCollabNotFoundError):
        _inspect_with_store(injector, store, collet.collet_id, binding_id)
    assert store.list_bindings(axes, collet.collet_id) == []
    assert store.list_composition_edges(axes, collet.collet_id) == []


@pytest.mark.parametrize("failure", ["second_binding", "composition_edge"])
def test_partial_instantiation_failure_propagates_and_recovers_pinned_topology(
    deploy_commission, monkeypatch, failure
):
    profile = _create_product_profile()
    _, _, injector = deploy_commission(profile)
    collet = _create_commission_collet(injector.get(ContextServiceProtocol))
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    with monkeypatch.context() as patch:
        if failure == "second_binding":
            original = store.upsert_binding

            def fail(axes, binding):
                if binding.method_names == ("Single",):
                    raise RuntimeError("binding persistence unavailable")
                return original(axes, binding)

            patch.setattr(store, "upsert_binding", fail)
        else:

            def fail(*args):
                raise RuntimeError("edge persistence unavailable")

            patch.setattr(store, "insert_composition_edge", fail)
        with pytest.raises(RuntimeError, match="persistence unavailable"):
            injector.get(ProductInstantiationServiceProtocol).instantiate(
                ProductInstantiateCommand(
                    collet.collet_id,
                    profile.ref,
                    collet.version,
                    participant_refs=(AgentRef("bot", BOT_ASSIGNEE),),
                ),
                HUMAN,
            )
    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        collet.collet_id
    )
    instance = record.product_instance
    # SQL selection was committed. Retry repairs projections, never replaces it.
    before = {
        item.method_binding_id: item
        for item in store.list_bindings(axes, collet.collet_id)
    }
    assert len(before) == (1 if failure == "second_binding" else 2)
    assert store.list_composition_edges(axes, collet.collet_id) == []
    _recovery(injector, store).recover(
        collet.collet_id,
        HUMAN,
        method_binding_id=instance.method_binding_refs[0],
        now=NOW,
    )
    assert len(store.list_bindings(axes, collet.collet_id)) == 2
    assert len(store.list_composition_edges(axes, collet.collet_id)) == 1
    for binding_id, binding in before.items():
        assert store.get_binding(axes, binding_id, collet.collet_id) == binding
    assert injector.get(A2aDeliveryPort).bridge_records() == ()
    assert (
        injector.get(ContextCollabRepositoryProtocol)
        .get_current_status(collet.collet_id)
        .product_instance
        == instance
    )


@pytest.mark.parametrize("state", [BindingState.SUSPENDED, BindingState.TERMINATED])
def test_recovery_preserves_existing_binding_lifecycle_and_detached_edge(
    instance_runtime, state
):
    _, _, injector, collet, instance = instance_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    binding_id = instance.method_binding_refs[1]
    binding = replace(
        store.get_binding(axes, binding_id, collet.collet_id), state=state
    )
    store.replace_binding(axes, binding)
    [edge] = store.list_composition_edges(axes, collet.collet_id)
    detached = replace(edge, state=CompositionEdgeState.DETACHED)
    store.replace_composition_edge(axes, detached)
    recovered = _recovery(injector, store).recover(
        collet.collet_id, HUMAN, method_binding_id=binding_id, now=NOW
    )
    assert recovered == binding
    assert store.get_composition_edge(axes, edge.edge_id, collet.collet_id) == detached
    assert store.list_composition_edges(axes, collet.collet_id) == []


def test_detached_composition_denies_execution_without_reactivation(admitted_runtime):
    _, bot, injector, collet, instance, decision = admitted_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    _select_admitted_agent(injector, store, axes, instance, decision)
    [edge] = store.list_composition_edges(axes, collet.collet_id)
    store.replace_composition_edge(
        axes, replace(edge, state=CompositionEdgeState.DETACHED)
    )
    _recovery(injector, store).recover(
        collet.collet_id,
        HUMAN,
        method_binding_id=instance.method_binding_refs[0],
        now=NOW,
    )
    with pytest.raises(
        ContextCollabValidationError, match="active product Method composition"
    ):
        injector.get(TaskMethodOpsPort).require_admitted(
            collet.collet_id, bot.actor_context
        )


def test_open_binding_cannot_own_single_selection(admitted_runtime):
    _, _, injector, collet, instance, decision = admitted_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    with pytest.raises(ContextCollabValidationError, match="Single Method binding"):
        injector.get(GroupMethodOpsServiceProtocol).select(
            collet.collet_id,
            instance.method_binding_refs[0],
            SingleSelectCommand(decision.resulting_binding_refs[0], collet.collet_id),
            HUMAN,
        )
    assert all(
        store.list_selections(axes, ref, collet.collet_id) == []
        for ref in instance.method_binding_refs
    )


def test_execution_http_requires_single_reference_not_open_reference(admitted_runtime):
    service, bot, injector, collet, instance, decision = admitted_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    _select_admitted_agent(injector, store, axes, instance, decision)
    repository = injector.get(ContextCollabRepositoryProtocol)
    started = injector.get(ContextServiceProtocol).start(
        collet.collet_id,
        WorkStartCommand(
            repository.get_current_status(collet.collet_id).collet.version,
            objective=OBJECTIVE,
        ),
        bot.actor_context,
    )
    created = TaskCommissionCreatedView(
        collet.collet_id,
        "",
        "",
        "in_progress",
        started.context_version,
    )
    payload = {"percent": 25, "note": "working", "progress": "in progress"}
    request = bot.report_request(created, action="progress", payload=payload)
    request["params"]["message"]["parts"][0]["data"]["context"]["method_binding_ref"][
        "object_id"
    ] = instance.method_binding_refs[0]
    bot.request(request, expected_status="REJECTED")
    before = repository.get_current_status(collet.collet_id)
    assert before.task.status == "in_progress"
    bot.request(bot.report_request(created, action="progress", payload=payload))
    after = repository.get_current_status(collet.collet_id)
    assert after.task.status == "in_progress"
    assert after.task.version == before.task.version + 1
    assert after.events[-1].event_kind == "task.progress.updated"


@pytest.mark.parametrize(
    "invalid", ["missing", "unknown_endpoint", "wrong_source", "duplicate", "cycle"]
)
def test_profile_rejects_ambiguous_or_invalid_composition(invalid):
    profile = _create_product_profile()
    relation = profile.composition.relations[0]
    relations = {
        "missing": (),
        "unknown_endpoint": (replace(relation, target_method="Group"),),
        "wrong_source": (
            replace(relation, source_method="Open", target_method="Single"),
        ),
        "duplicate": (relation, relation),
        "cycle": (
            relation,
            MethodRelationTemplate("Single", "Single", CompositionRelation.SUPPORT),
        ),
    }[invalid]
    with pytest.raises(ContextCollabValidationError):
        ProductCatalog(
            (
                replace(
                    profile,
                    composition=replace(profile.composition, relations=relations),
                ),
            )
        )


def test_historical_composite_instance_is_not_silently_reinterpreted(
    instance_runtime, db
):
    _, _, injector, collet, _ = instance_runtime
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=collet.collet_id)
            .one()
        )
        facts = stored_context_value(row, "creation_facts")
        facts["product_instance"]["method_binding_refs"] = [
            top_level_binding_id(collet.collet_id)
        ]
        store_context_value(row, "creation_facts", facts)
    with pytest.raises(
        ContextCollabValidationError, match="persisted product bindings"
    ):
        injector.get(ContextCollabRepositoryProtocol).get_current_status(
            collet.collet_id
        )
