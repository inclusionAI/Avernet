"""Real product composition, selection prerequisites and ProductInstance persistence."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.context_state_machines import (
    COLLET_LIFECYCLE_MACHINE,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_BINDING_MACHINE,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    GroupMethodOpsServiceProtocol,
    MethodLifecycleServiceProtocol,
    MethodStateChangeCommand,
    OpenMethodOpsServiceProtocol,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
    composition_method_kind,
    negotiate,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    OPEN_SINGLE_COMMISSION_PROFILE,
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    ApplyForTaskCommand,
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskMethodOpsPort,
)
from agentclaw.community.di.modules.task.context_collab_products import (
    build_product_catalog,
)

from tests.community.repository.task.context_collab.context_spec_test_support import (
    store_context_value,
    stored_context_value,
)
from tests.community.core.task.context_collab.products.task_commission_e2e_support import (
    opportunities_query_request,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    HUMAN,
    OBJECTIVE,
    SPACE,
    _admit_application_agent,
    _create_commission_collet,
    _create_product_profile,
    _discover_opportunity,
    _instantiate_product_instance,
    _publish_opportunity,
    _select_admitted_agent,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    commission_deployment as _commission_deployment_fixture,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end import (
    deploy_commission as _deploy_commission_fixture,
)

# Re-export the existing runtime fixtures without reimplementing their DI/SQL setup.
commission_deployment = _commission_deployment_fixture
deploy_commission = _deploy_commission_fixture


def test_open_single_is_an_explicit_new_version_not_a_legacy_upgrade():
    catalog = build_product_catalog()
    assert catalog.get("task_commission", 2) == OPEN_SINGLE_COMMISSION_PROFILE
    assert (
        catalog.get("task_commission", 1).composition
        == TASK_COMMISSION_PROFILE.composition
    )
    assert composition_method_kind(TASK_COMMISSION_PROFILE.composition) == "Open"
    assert (
        composition_method_kind(OPEN_SINGLE_COMMISSION_PROFILE.composition)
        == "Open + Single"
    )


@pytest.mark.parametrize("missing_method", ["Open", "Single"])
def test_open_single_cannot_negotiate_without_both_methods(missing_method):
    catalog = build_product_catalog()
    capabilities = catalog.capabilities()
    result = negotiate(
        OPEN_SINGLE_COMMISSION_PROFILE,
        replace(
            capabilities,
            methods=tuple(
                item for item in capabilities.methods if item.method != missing_method
            ),
        ),
    )
    assert not result.accepted
    assert result.missing_capabilities == (f"method:{missing_method}@0.2",)


@pytest.mark.parametrize("machine", [COLLET_LIFECYCLE_MACHINE, METHOD_BINDING_MACHINE])
def test_shared_lifecycles_declare_open_single(machine):
    assert all("Open + Single" in state.allowed_methods for state in machine.states)


@pytest.fixture
def instance_runtime(deploy_commission):
    profile = _create_product_profile()
    service, bot, injector = deploy_commission(profile)
    collet = _create_commission_collet(injector.get(ContextServiceProtocol))
    instance = _instantiate_product_instance(injector, collet, profile)
    return service, bot, injector, collet, instance


@pytest.fixture
def admitted_runtime(instance_runtime):
    service, bot, injector, collet, instance = instance_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    open_ops = injector.get(OpenMethodOpsServiceProtocol)
    _publish_opportunity(open_ops, collet.collet_id)
    opportunity, application = _discover_opportunity(store, axes, collet, bot)
    profile = _create_product_profile()
    decision = _admit_application_agent(
        injector,
        open_ops,
        store,
        axes,
        collet.collet_id,
        opportunity,
        application,
        profile,
    )
    return service, bot, injector, collet, instance, decision


def test_product_apply_orchestrates_admission_then_selection_before_work(
    deploy_commission,
):
    profile = _create_product_profile()
    service, bot, injector = deploy_commission(profile)
    created = service.publish_task(
        PublishTaskCommand(
            title="Open + Single",
            body="自主申请后选定执行者",
            space=SPACE,
            assignee_agent_id=bot.bot_uuid,
            product_profile_ref=profile.ref,
        ),
        HUMAN,
    )
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    repository = injector.get(ContextCollabRepositoryProtocol)
    instance = repository.get_current_status(created.collet_id).product_instance
    binding_id, single_binding_id = instance.method_binding_refs
    [opportunity] = service.query_opportunities(profile.ref, bot.actor_context)
    assert store.list_selections(axes, single_binding_id, created.collet_id) == []
    assert store.list_applications(axes, binding_id, created.collet_id) == []
    receipt = service.apply_for_task(
        ApplyForTaskCommand(
            collab_context_id=created.collet_id,
            opportunity_id=opportunity.opportunity_id,
            method_binding_ref=binding_id,
            product_profile_ref=profile.ref,
            accountable_principal_id=bot.owner_id,
        ),
        bot.actor_context,
    )
    assert receipt.state == "ADMITTED"
    [selection] = store.list_selections(axes, single_binding_id, created.collet_id)
    assert selection.selected_agent_participant_ref in receipt.resulting_binding_refs
    assert {receipt.application_id, receipt.decision_id} <= set(
        selection.selection_evidence_refs
    )
    service.start_task(TaskAdvanceCommand(created.collet_id), bot.actor_context)
    assert service.detail_task(created.collet_id).status == "in_progress"
    assert repository.get_current_status(created.collet_id).product_instance == instance


def test_signed_http_query_apply_selects_before_dispatch(
    deploy_commission, monkeypatch
):
    profile = _create_product_profile()
    service, bot, injector = deploy_commission(profile)
    created = service.publish_task(
        PublishTaskCommand(
            title="HTTP Open + Single",
            body="签名申请后选定",
            space=SPACE,
            assignee_agent_id=bot.bot_uuid,
            product_profile_ref=profile.ref,
        ),
        HUMAN,
    )
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    instance = (
        injector.get(ContextCollabRepositoryProtocol)
        .get_current_status(created.collet_id)
        .product_instance
    )
    binding_id, single_binding_id = instance.method_binding_refs
    queried = bot.request(opportunities_query_request(bot))
    [opportunity] = queried["data"]["opportunities"]
    assert opportunity["collab_context_id"] == instance.collet_ref
    assert opportunity["method_binding_ref"] == binding_id
    assert store.list_applications(axes, binding_id, created.collet_id) == []
    delivery = injector.get(A2aDeliveryPort)
    send_message, sent = delivery.send_message, []

    def capture_dispatch(card, message, envelope):
        sent.append(envelope)
        return send_message(card, message, envelope)

    monkeypatch.setattr(delivery, "send_message", capture_dispatch)
    applied = bot.request(bot.application_request(opportunity))
    receipt = applied["data"]["admission"]
    assert receipt["state"] == "ADMITTED"
    [selection] = store.list_selections(axes, single_binding_id, created.collet_id)
    assert selection.selected_agent_participant_ref in receipt["resulting_binding_refs"]
    assert {receipt["application_id"], receipt["decision_id"]} <= set(
        selection.selection_evidence_refs
    )
    [bridge] = injector.get(A2aDeliveryPort).bridge_records()
    assert bridge.direction == "OUTBOUND"
    [dispatch] = sent
    assert dispatch.context.method_binding_ref.object_id == single_binding_id
    assert store.list_selections(axes, binding_id, created.collet_id) == []
    assert store.list_opportunities(axes, single_binding_id, created.collet_id) == []
    injector.get(TaskMethodOpsPort).require_admitted(
        created.collet_id, bot.actor_context
    )
    assert service.detail_task(created.collet_id).status == "created"


def test_selection_failure_propagates_without_dispatch_or_execution(
    deploy_commission, monkeypatch
):
    profile = _create_product_profile()
    service, bot, injector = deploy_commission(profile)
    created = service.publish_task(
        PublishTaskCommand(
            title="Selection failure",
            body="Do not dispatch without selection",
            space=SPACE,
            assignee_agent_id=bot.bot_uuid,
            product_profile_ref=profile.ref,
        ),
        HUMAN,
    )
    repository = injector.get(ContextCollabRepositoryProtocol)
    instance = repository.get_current_status(created.collet_id).product_instance
    [opportunity] = service.query_opportunities(profile.ref, bot.actor_context)

    def fail_selection(*args, **kwargs):
        raise RuntimeError("selection persistence unavailable")

    monkeypatch.setattr(
        injector.get(GroupMethodOpsServiceProtocol), "select", fail_selection
    )
    with pytest.raises(RuntimeError, match="selection persistence unavailable"):
        service.apply_for_task(
            ApplyForTaskCommand(
                collab_context_id=created.collet_id,
                opportunity_id=opportunity.opportunity_id,
                method_binding_ref=opportunity.method_binding_ref,
                product_profile_ref=profile.ref,
                accountable_principal_id=bot.owner_id,
            ),
            bot.actor_context,
        )
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    binding_id, single_binding_id = instance.method_binding_refs
    [application] = store.list_applications(axes, binding_id, created.collet_id)
    # Admission is already written; this is not a cross-store transaction/rollback.
    assert application.state.value == "ADMITTED"
    assert store.list_selections(axes, single_binding_id, created.collet_id) == []
    assert injector.get(A2aDeliveryPort).bridge_records() == ()
    with pytest.raises(ContextCollabValidationError, match="Single selection"):
        service.start_task(TaskAdvanceCommand(created.collet_id), bot.actor_context)
    assert service.detail_task(created.collet_id).status == "created"
    assert repository.get_current_status(created.collet_id).product_instance == instance


@pytest.mark.parametrize(
    "invalid",
    [
        "other_scope",
        "agent_id",
        "missing_evidence",
        "expired",
        "bot_selector",
        "invalid_window",
        "naive_window",
    ],
)
def test_admission_and_unrelated_selection_cannot_authorize_work(
    admitted_runtime, invalid
):
    service, bot, injector, collet, instance, decision = admitted_runtime
    command = SingleSelectCommand(
        agent_participant_ref=decision.resulting_binding_refs[0],
        scope_ref=instance.collet_ref,
        selection_evidence_refs=[
            decision.application_ref,
            decision.admission_decision_id,
        ],
    )
    if invalid == "other_scope":
        command = replace(command, scope_ref="another_collet")
    elif invalid == "agent_id":
        command = replace(command, agent_participant_ref=bot.bot_uuid)
    elif invalid == "missing_evidence":
        command = replace(command, selection_evidence_refs=[])
    elif invalid == "invalid_window":
        command = replace(command, valid_until="not-a-timestamp")
    elif invalid == "naive_window":
        command = replace(command, valid_until="2099-01-01T00:00:00")
    elif invalid == "expired":
        command = replace(
            command,
            valid_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
    injector.get(GroupMethodOpsServiceProtocol).select(
        collet.collet_id,
        instance.method_binding_refs[1],
        command,
        bot.actor_context if invalid == "bot_selector" else HUMAN,
    )
    repository = injector.get(ContextCollabRepositoryProtocol)
    before = repository.get_current_status(collet.collet_id)
    assert before.task is None
    with pytest.raises(ContextCollabValidationError, match="Single selection"):
        injector.get(ContextServiceProtocol).start(
            collet.collet_id,
            WorkStartCommand(
                expected_version=before.collet.version, objective=OBJECTIVE
            ),
            bot.actor_context,
        )
    assert repository.get_current_status(collet.collet_id) == before


def test_releasing_selection_revokes_execution_but_preserves_instance(admitted_runtime):
    service, bot, injector, collet, instance, decision = admitted_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    _select_admitted_agent(injector, store, axes, instance, decision)
    injector.get(TaskMethodOpsPort).require_admitted(
        collet.collet_id, bot.actor_context
    )
    injector.get(GroupMethodOpsServiceProtocol).release(
        collet.collet_id,
        instance.method_binding_refs[1],
        MethodStateChangeCommand(),
        HUMAN,
    )
    repository = injector.get(ContextCollabRepositoryProtocol)
    before = repository.get_current_status(collet.collet_id)
    assert before.task is None
    with pytest.raises(ContextCollabValidationError, match="Single selection"):
        injector.get(ContextServiceProtocol).start(
            collet.collet_id,
            WorkStartCommand(before.collet.version, objective=OBJECTIVE),
            bot.actor_context,
        )
    assert repository.get_current_status(collet.collet_id) == before
    assert before.product_instance == instance


@pytest.mark.parametrize("method_index, method", [(0, "Open"), (1, "Single")])
def test_suspended_binding_denies_work_without_mutating_peer_or_instance(
    admitted_runtime,
    method_index,
    method,
):
    _, bot, injector, collet, instance, decision = admitted_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    _select_admitted_agent(injector, store, axes, instance, decision)
    methods = injector.get(MethodLifecycleServiceProtocol)
    args = (
        collet.collet_id,
        instance.method_binding_refs[method_index],
        MethodStateChangeCommand(),
        HUMAN,
    )
    peer_id = instance.method_binding_refs[1 - method_index]
    peer = store.get_binding(axes, peer_id, collet.collet_id)
    methods.suspend(*args)
    assert store.get_binding(axes, peer_id, collet.collet_id) == peer
    with pytest.raises(
        ContextCollabValidationError, match=f"active {method} Method binding"
    ):
        injector.get(TaskMethodOpsPort).require_admitted(
            collet.collet_id, bot.actor_context
        )
    methods.resume(*args)
    injector.get(TaskMethodOpsPort).require_admitted(
        collet.collet_id, bot.actor_context
    )
    assert (
        injector.get(ContextCollabRepositoryProtocol)
        .get_current_status(collet.collet_id)
        .product_instance
        == instance
    )


def test_instance_keeps_compact_exact_method_references_not_contract_copies(
    instance_runtime,
):
    _, _, injector, collet, instance = instance_runtime
    assert instance.to_dict()["method_profiles"] == [
        {"method": "Open", "version": "0.2"},
        {"method": "Single", "version": "0.2"},
    ]
    restored = (
        injector.get(ContextCollabRepositoryProtocol)
        .get_current_status(collet.collet_id)
        .product_instance
    )
    assert restored == instance
    assert [(item.method, item.version) for item in restored.method_profiles] == [
        ("Open", "0.2"),
        ("Single", "0.2"),
    ]


@pytest.mark.parametrize("root_ref", ["another_task", ""])
def test_wrong_persisted_root_task_reference_fails_closed(
    instance_runtime, db, root_ref
):
    _, _, injector, collet, _ = instance_runtime
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=collet.collet_id)
            .one()
        )
        facts = stored_context_value(row, "creation_facts")
        facts["product_instance"]["root_task_ref"] = root_ref
        store_context_value(row, "creation_facts", facts)
    with pytest.raises(ContextCollabValidationError, match="ProductInstance"):
        injector.get(ContextCollabRepositoryProtocol).get_current_status(
            collet.collet_id
        )


def test_historical_instance_without_root_ref_uses_l4_identity_without_rewriting(
    admitted_runtime, db
):
    _, bot, injector, collet, instance, decision = admitted_runtime
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    _select_admitted_agent(injector, store, axes, instance, decision)
    repository = injector.get(ContextCollabRepositoryProtocol)
    injector.get(ContextServiceProtocol).start(
        collet.collet_id,
        WorkStartCommand(
            repository.get_current_status(collet.collet_id).collet.version,
            objective=OBJECTIVE,
        ),
        bot.actor_context,
    )
    instance = repository.get_current_status(collet.collet_id).product_instance
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=collet.collet_id)
            .one()
        )
        facts = stored_context_value(row, "creation_facts")
        del facts["product_instance"]["root_task_ref"]
        store_context_value(row, "creation_facts", facts)
    recovered = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        collet.collet_id
    )
    assert recovered.product_instance == instance
    assert recovered.product_instance.root_task_ref == recovered.task.task_id
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=collet.collet_id)
            .one()
        )
        assert stored_context_value(row, "creation_facts") == facts


def test_product_selection_cannot_be_replaced_on_an_existing_instance(instance_runtime):
    _, _, injector, collet, instance = instance_runtime
    with pytest.raises(ContextCollabValidationError, match="untouched L4 Collet"):
        injector.get(ProductInstantiationServiceProtocol).instantiate(
            ProductInstantiateCommand(
                collet.collet_id,
                VersionedProfileRef("task_commission", 1),
                injector.get(ContextCollabRepositoryProtocol)
                .get_current_status(collet.collet_id)
                .collet.version,
            ),
            HUMAN,
        )
    assert (
        injector.get(ContextCollabRepositoryProtocol)
        .get_current_status(collet.collet_id)
        .product_instance
        == instance
    )
