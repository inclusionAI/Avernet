"""Repository-backed evidence: pinned selections and no generic/product bypass."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    prepare_product_spec,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
    CreateContextRecordRequest,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCreateCommand,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    current_axes,
    ensure_top_level_binding,
)
from agentclaw.community.core.task.context_collab.services.open_method_service import (
    OpenMethodService,
)
from agentclaw.community.di.modules.task_commission_method_ops_adapter import (
    TaskCommissionOpenMethodAdapter,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

OWNER = PrincipalRef("human", "owner")
BOT = PrincipalRef("bot", "worker")
SPACE = SpaceRef("space", "test")


def _actor(principal=OWNER):
    return ActorContext(actor=principal, source="api")


def _command(**overrides):
    fields = dict(
        space=SPACE,
        principal_ref=OWNER,
        method_kind="Open",
        executor_ref=AgentRef("bot", "worker"),
        objective="deliver",
        resource_refs=[],
        product_kind="task_commission",
        workflow_kind="task_commission_goal",
    )
    return ContextCreateCommand(**(fields | overrides))


def _services(db, dispatch_port=None, *, catalog=PRODUCT_CATALOG):
    repo = ContextCollabRepository(db, product_catalog=catalog)
    store = InMemoryMethodStore()
    authorizer = StructuralContextCollabAuthorizer()
    generic = ContextCollabService(
        repository=repo,
        method_store=store,
        authorizer=authorizer,
        product_catalog=catalog,
    )
    port = TaskCommissionOpenMethodAdapter(
        open_ops=OpenMethodService(
            store=store, authorizer=StructuralMethodOpsAuthorizer(), collab_repo=repo
        ),
        product_catalog=catalog,
    )
    product = TaskCommissionService(
        collab_repo=repo,
        method_store=store,
        authorizer=authorizer,
        method_ops_port=port,
        dispatch_port=dispatch_port,
        product_catalog=catalog,
    )
    return repo, store, generic, product


def _append(
    service, collet_id, version, event="task.started", actor=OWNER, **overrides
):
    fields = dict(
        expected_version=version,
        event_kind=event,
        primitive_type="Task",
        actor_ref=actor,
        parent_event=None,
        visibility="public",
        payload={},
    )
    return service.append_event(
        collet_id, EventAppendCommand(**(fields | overrides)), _actor(actor)
    )


def _counts(db):
    with db.orm_session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (ContextCollabColletModel, ContextCollabEventModel)
        )


@pytest.mark.parametrize(
    "overrides",
    [
        dict(workflow_kind="bbs_public_discussion"),
        dict(method_kind="Single"),
        dict(executor_ref=None),
        dict(product_kind="not_registered"),
        dict(product_kind=None, workflow_kind="not_registered"),
    ],
)
def test_invalid_product_composition_rejected_before_any_write(db, overrides):
    _, store, service, _ = _services(db)
    with pytest.raises(ContextCollabValidationError):
        service.create_context(_command(**overrides), _actor())
    assert _counts(db) == (0, 0)
    assert store.list_bindings(current_axes(), "missing") == []


def test_both_entrypoints_use_same_executor_and_sequence_contract(db):
    repo, _, generic, product = _services(db)
    created = product.publish_task(
        PublishTaskCommand("deliver", "details", SPACE, "worker"), _actor()
    )
    collet_id = created.collet_id
    assert repo.get_current_status(collet_id).product_instance is not None
    command = TaskAdvanceCommand(collet_id, artifacts=["artifact-1"])
    for attempt in (
        lambda: product.submit_task(command, _actor(BOT)),
        lambda: _append(
            generic,
            collet_id,
            2,
            "task.submitted",
            BOT,
            payload={"artifacts": ["artifact-1"]},
        ),
    ):
        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            attempt()
    assert _counts(db) == (1, 2)
    started = _append(generic, collet_id, 2, actor=BOT)
    for attempt in (
        lambda: product.submit_task(command, _actor()),
        lambda: _append(
            generic,
            collet_id,
            started.context_version,
            "task.submitted",
            payload={"artifacts": ["artifact-1"]},
        ),
    ):
        with pytest.raises(ContextCollabValidationError, match="designated executor"):
            attempt()
    assert _counts(db) == (1, 3)
    submitted = product.submit_task(command, _actor(BOT))
    assert submitted.status == "reviewing"
    accepted = _append(generic, collet_id, submitted.context_version, "task.accepted")
    assert accepted.current_status == "done"
    for attempt in (
        lambda: product.start_task(TaskAdvanceCommand(collet_id), _actor(BOT)),
        lambda: _append(generic, collet_id, accepted.context_version, actor=BOT),
    ):
        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            attempt()
    assert _counts(db) == (1, 5)


def test_generic_message_is_nontransition_but_stale_control_is_conflict(db):
    _, _, service, _ = _services(db)
    created = service.create_context(_command(), _actor())
    started = _append(service, created.collab_context_id, 1)
    message = _append(
        service,
        created.collab_context_id,
        started.context_version,
        "user_interaction.posted",
        primitive_type="Message",
        payload={"content": "update"},
    )
    assert message.current_status == "in_progress"
    with pytest.raises(ContextCollabVersionConflict):
        _append(service, created.collab_context_id, 1)
    assert _counts(db) == (1, 3)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(primitive_type="Message"),
        dict(visibility="private"),
        dict(payload={"percent": 12}),
        dict(event_kind="task.completed"),
    ],
)
def test_generic_control_cannot_bypass_product_shape(db, overrides):
    _, _, service, _ = _services(db)
    created = service.create_context(_command(), _actor())
    with pytest.raises(ContextCollabValidationError):
        _append(service, created.collab_context_id, 1, **overrides)
    assert _counts(db) == (1, 1)


@pytest.mark.parametrize(
    "impostor", [PrincipalRef("human", "worker"), PrincipalRef("bot", "owner")]
)
def test_pinned_l4_roles_use_typed_identity_not_just_same_id(db, impostor):
    _, _, service, _ = _services(db)
    created = service.create_context(_command(), _actor())
    with pytest.raises(ContextCollabValidationError, match="only the principal"):
        _append(service, created.collab_context_id, 1, actor=impostor)
    assert _counts(db) == (1, 1)


def test_pinned_instance_round_trip_and_heal_do_not_renegotiate(db, monkeypatch):
    repo, store, service, _ = _services(db)
    created = service.create_context(_command(), _actor())
    record = repo.get_current_status(created.collab_context_id)
    instance = record.product_instance
    assert instance.profile_ref.version == instance.workflow_ref.version == 1
    assert instance.creation_event_ref == created.current_event["event_id"]
    binding = store.get_binding(
        current_axes(), instance.method_binding_refs[0], created.collab_context_id
    )
    assert binding.role_binding_refs == instance.role_binding_refs
    assert binding.profile_versions == {
        method.method: method.version for method in instance.method_profiles
    }
    assert (
        len(record.context_spec.participants)
        == len(record.context_spec.role_bindings)
        == 2
    )
    # Roles are collaboration-only; creation did not manufacture L3 grants.
    assert record.context_spec.resource_refs == ()
    assert set(record.context_spec.to_dict()) == {
        "space",
        "principal_ref",
        "method_kind",
        "executor_ref",
        "objective",
        "resource_refs",
        "product_kind",
        "workflow_kind",
        "product_profile_version",
        "workflow_profile_version",
        "participants",
        "role_bindings",
    }

    def cannot_negotiate(_self):
        pytest.fail(
            "historical read must not recompute negotiation against installed capabilities"
        )

    monkeypatch.setattr(ProductCatalog, "capabilities", cannot_negotiate)
    restarted_repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
    recovered = restarted_repo.get_current_status(created.collab_context_id)
    assert recovered.product_instance == instance
    healed = ensure_top_level_binding(
        InMemoryMethodStore(),
        current_axes(),
        created.collab_context_id,
        restarted_repo,
        _actor(),
        now="2026-11-13T00:00:00+00:00",
        catalog=PRODUCT_CATALOG,
    )
    assert healed.profile_versions == binding.profile_versions
    assert healed.role_binding_refs == binding.role_binding_refs


@pytest.mark.parametrize(
    "corruption",
    ["missing_instance", "role", "version", "evidence", "method", "negotiation"],
)
def test_corrupt_pinned_metadata_fails_closed(db, corruption):
    repo, _, service, _ = _services(db)
    created = service.create_context(_command(), _actor())
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.context_spec)
        if corruption == "missing_instance":
            del data["product_instance"]
        elif corruption == "role":
            data["role_bindings"] = []
        elif corruption == "version":
            data["product_profile_version"] = 999
        elif corruption == "evidence":
            data["product_instance"]["creation_event_ref"] = "not-the-initial-event"
        elif corruption == "method":
            data["product_instance"]["method_binding_refs"] = ["not-the-binding"]
        else:
            data["product_instance"]["negotiation"]["missing_capabilities"] = [
                "method:Open@0.2"
            ]
        row.context_spec = json.dumps(data)
    with pytest.raises(ContextCollabValidationError):
        repo.get_current_status(created.collab_context_id)


def test_legacy_rows_remain_readable_without_fabricated_negotiation(db):
    repo, _, service, _ = _services(db)
    created = service.create_context(_command(), _actor())
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.context_spec)
        for key in (
            "product_profile_version",
            "workflow_profile_version",
            "participants",
            "role_bindings",
            "product_instance",
        ):
            del data[key]
        row.context_spec = json.dumps(data)
    record = repo.get_current_status(created.collab_context_id)
    assert record.product_instance is None
    assert record.context_spec.product_profile_version is None
    assert record.context_spec.to_dict() == data
    assert _counts(db) == (1, 1)


def test_unprofiled_generic_context_keeps_legacy_wire_contract(db):
    repo, _, service, _ = _services(db)
    created = service.create_context(
        _command(product_kind=None, workflow_kind=None), _actor()
    )
    record = repo.get_current_status(created.collab_context_id)
    assert record.product_instance is None
    assert "product_profile_version" not in record.context_spec.to_dict()
    assert set(record.events[0].payload) == {
        "objective",
        "resource_refs",
        "method_kind",
        "executor_ref",
        "product_kind",
        "workflow_kind",
    }


def test_bbs_generic_cannot_reply_to_task_skeleton(db):
    _, _, service, _ = _services(db)
    created = service.create_context(
        _command(
            product_kind="bbs_public_discussion",
            workflow_kind="bbs_public_discussion",
            method_kind="Open + Group",
            executor_ref=None,
        ),
        _actor(),
    )
    with pytest.raises(ContextCollabValidationError, match="not a Message"):
        _append(
            service,
            created.collab_context_id,
            1,
            "user_interaction.posted",
            primitive_type="Message",
            payload={"content": "reply"},
            parent_event=created.current_event["event_id"],
        )
    assert _counts(db) == (1, 1)


def test_message_cannot_smuggle_commission_progress_evidence(db):
    _, _, service, _ = _services(db)
    created = service.create_context(_command(), _actor())
    with pytest.raises(ContextCollabValidationError, match="only reportable"):
        _append(
            service,
            created.collab_context_id,
            1,
            "user_interaction.posted",
            primitive_type="Message",
            payload={"content": "update", "percent": 50},
        )
    assert _counts(db) == (1, 1)


@pytest.mark.parametrize("legacy", [False, True])
def test_product_work_uses_exact_machine_even_for_legacy_v1(db, monkeypatch, legacy):
    from agentclaw.community.core.task.context_collab.g4_contracts_consistency import (
        state_machine,
    )

    _, _, generic, product = _services(db)
    created = generic.create_context(_command(), _actor())
    if legacy:
        with db.orm_session() as session:
            row = session.query(ContextCollabColletModel).one()
            data = json.loads(row.context_spec)
            for key in (
                "product_profile_version",
                "workflow_profile_version",
                "participants",
                "role_bindings",
                "product_instance",
            ):
                del data[key]
            row.context_spec = json.dumps(data)
    monkeypatch.setitem(state_machine.MACHINE_REGISTRY, "task_commission_goal", None)
    started = product.start_task(
        TaskAdvanceCommand(created.collab_context_id), _actor(BOT)
    )
    assert started.status == "in_progress"


def test_commission_dispatch_correlates_persisted_selection_and_actual_l5_binding(db):
    class CaptureDispatch:
        def __init__(self):
            self.sent = []

        def dispatch_task_info(self, info):
            self.sent.append(info)

    delivery = CaptureDispatch()
    repo, store, _, product = _services(db, dispatch_port=delivery)
    created = product.publish_task(
        PublishTaskCommand("deliver", "details", SPACE, "worker"), _actor()
    )
    record = repo.get_current_status(created.collet_id)
    instance = record.product_instance
    [sent] = delivery.sent
    assert sent.product_profile_ref == instance.profile_ref
    assert (sent.method_binding_ref,) == instance.method_binding_refs
    binding = store.get_binding(
        current_axes(), sent.method_binding_ref, created.collet_id
    )
    assert binding.role_binding_refs == instance.role_binding_refs
    [application] = store.list_applications(
        current_axes(), sent.method_binding_ref, created.collet_id
    )
    assert application.state.value == "ADMITTED"
    assert record.current_snapshot.status == "created"  # Delivery != completion.


def test_uninstalled_catalog_rejects_both_creators_before_any_write(db):
    _, store, generic, product = _services(db, catalog=ProductCatalog(()))
    for create in (
        lambda: generic.create_context(_command(), _actor()),
        lambda: product.publish_task(
            PublishTaskCommand("deliver", "details", SPACE, "worker"), _actor()
        ),
    ):
        with pytest.raises(ContextCollabValidationError, match="unknown product"):
            create()
    assert _counts(db) == (0, 0)
    assert store.list_bindings(current_axes(), "missing") == []


def test_injected_exact_v2_survives_storage_recovery_and_shared_g4(db, monkeypatch):
    # No v1 is installed: consulting a global/default catalog cannot pass this test.
    # v2 deliberately allows private messages, unlike the installed v1 contract.
    v2 = replace(
        TASK_COMMISSION_PROFILE,
        ref=VersionedProfileRef("task_commission", 2),
        domain=replace(
            TASK_COMMISSION_PROFILE.domain,
            ref=VersionedProfileRef("custom.commission.domain", 2),
        ),
        workflow=replace(TASK_COMMISSION_PROFILE.workflow, public_events_only=False),
    )
    catalog = ProductCatalog((v2,))
    repo, _, _, _ = _services(db, catalog=catalog)
    spec = prepare_product_spec(
        ContextSpec(
            space=SPACE,
            principal_ref=OWNER,
            method_kind="Open",
            executor_ref=AgentRef("bot", "worker"),
            objective="deliver",
            resource_refs=(),
            product_kind="task_commission",
            workflow_kind="task_commission_goal",
        ),
        catalog,
        selected_profile=v2.ref,
    )
    created = repo.create_context(
        CreateContextRecordRequest(
            context_spec=spec,
            initial_event=InitialEventDraft(
                event_kind="task.created",
                primitive_type="Task",
                actor_ref=OWNER,
                payload={"objective": "deliver"},
            ),
        )
    )
    collet_id = created.collet.collet_id
    instance = repo.get_current_status(collet_id).product_instance
    assert instance.profile_ref == v2.ref
    assert instance.domain_ref == v2.domain.ref

    def cannot_negotiate(_self):
        pytest.fail("historical read/recovery must not renegotiate")

    monkeypatch.setattr(ProductCatalog, "capabilities", cannot_negotiate)
    restarted_repo, recovered_store, generic, product = _services(db, catalog=catalog)
    record = restarted_repo.get_current_status(collet_id)
    assert record.product_instance == instance
    healed = ensure_top_level_binding(
        recovered_store,
        current_axes(),
        collet_id,
        restarted_repo,
        _actor(),
        now="2026-11-13T00:00:00+00:00",
        catalog=catalog,
    )
    assert healed.profile_versions == {"Open": "0.2"}
    assert healed.role_binding_refs == instance.role_binding_refs
    assert (healed.method_binding_id,) == instance.method_binding_refs
    # Generic G4 must use injected v2, not reject using v1's public-only rule.
    message = _append(
        generic,
        collet_id,
        1,
        "user_interaction.posted",
        primitive_type="Message",
        visibility="private",
        payload={"content": "note"},
    )
    assert message.current_status == "created"
    started = product.start_task(TaskAdvanceCommand(collet_id), _actor(BOT))
    assert started.context_version == 3
    assert restarted_repo.get_current_status(collet_id).product_instance == instance
    assert _counts(db) == (1, 3)


def test_legacy_commission_progress_requires_typed_executor(db):
    _, _, generic, product = _services(db)
    created = generic.create_context(_command(), _actor())
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.context_spec)
        for key in (
            "product_profile_version",
            "workflow_profile_version",
            "participants",
            "role_bindings",
            "product_instance",
        ):
            del data[key]
        row.context_spec = json.dumps(data)
    product.start_task(TaskAdvanceCommand(created.collab_context_id), _actor(BOT))
    for principal in (OWNER, PrincipalRef("human", BOT.principal_id)):
        with pytest.raises(ContextCollabValidationError, match="designated executor"):
            product.report_progress(
                TaskAdvanceCommand(created.collab_context_id, percent=20),
                _actor(principal),
            )
    progressed = product.report_progress(
        TaskAdvanceCommand(created.collab_context_id, percent=20),
        _actor(BOT),
    )
    assert progressed.context_version == 3
