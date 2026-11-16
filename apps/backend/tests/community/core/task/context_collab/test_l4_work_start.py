"""Explicit L4 Work.start: real SQL authority, G1/G4 and atomic revision fences."""

from dataclasses import replace

import pytest
from sqlalchemy import event as sql_event

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency import (
    workflow_resolution,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
    ContextCollabService as ApplicationCollabService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_policy import (
    ProductCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    WorkStartCommand as CanonicalWorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
    WorkflowProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.products.legacy_profiles import (
    LEGACY_PRODUCT_PROFILES,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.service_api import (
    CollaborationCreateCommand,
    EventAppendCommand,
    WorkStartCommand,
)

from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = ActorContext(PrincipalRef("bot", "worker"), "a2a")


@pytest.fixture(params=[False, True], ids=["standard_task", "explicit_workflow"])
def runtime(db, request):
    # Isolate Work from admission; the real DI journey tests scoped admission.
    contract = STANDARD_TASK_CONTRACT
    workflow = (
        WorkflowProfile(
            ref=VersionedProfileRef("test_work_start.workflow", 1),
            state_machine_ref=contract.state_machine_ref,
            completion_states=contract.completion_states,
            compensation_events=contract.compensation_events,
            strict_control_events=True,
            executor_submission_only=True,
            public_events_only=True,
            actions=contract.actions,
        )
        if request.param
        else None
    )
    profile = create_product_profile(
        replace(
            TASK_COMMISSION_PROFILE,
            ref=VersionedProfileRef("test_work_start", 1),
            workflow=workflow,
            publication=None,
        )
    )
    catalog = ProductCatalog((profile,))
    repo = ContextCollabRepository(db, product_catalog=catalog)
    composer = ApplicationCollabService(
        repo, InMemoryMethodStore(), product_catalog=catalog
    )
    collet = composer.create_collaboration(
        CollaborationCreateCommand(
            to_space=SpaceRef("work-space", "test"),
            principal_ref=OWNER.actor,
            product_kind=profile.ref.profile_id,
            workflow_kind=workflow.ref.profile_id if workflow else None,
            method_kind=profile.composition.method_kind,
            executor_ref=AgentRef("bot", "worker"),
            objective="交付可验收的工作成果",
            resource_refs=[],
            product_profile_ref=profile.ref,
        ),
        OWNER,
    )
    authorizer = StructuralContextCollabAuthorizer()
    service = ContextService(
        repo, ProductCollaborationPolicy(catalog, authorizer), authorizer
    )
    return service, repo, collet.collet_id


def _append(runtime, kind, actor=BOT, *, primitive="Task", payload=None):
    service, repo, cid = runtime
    current = repo.get_current_status(cid)
    return service.append_event(
        cid,
        EventAppendCommand(
            expected_version=current.collet.version,
            event_kind=kind,
            primitive_type=primitive,
            actor_ref=actor.actor,
            parent_event=None,
            visibility="public",
            payload=payload or {},
        ),
        actor,
    )


def _reject_unchanged(runtime, attempt, error=ContextCollabValidationError):
    _, repo, cid = runtime
    before = repo.get_current_status(cid)
    with pytest.raises(error):
        attempt()
    assert repo.get_current_status(cid) == before


@pytest.mark.parametrize("version", [True, False, 0, -1, 1.0, "1", None])
def test_start_requires_positive_integer_revision(version):
    with pytest.raises(ContextCollabValidationError, match="expected_version"):
        WorkStartCommand(expected_version=version)


@pytest.mark.parametrize("note", [None, 1, {}, []])
def test_start_requires_string_note(note):
    with pytest.raises(ContextCollabValidationError, match="note"):
        WorkStartCommand(expected_version=1, note=note)


@pytest.mark.parametrize("note", ["", "录取后启动任务执行"])
def test_start_updates_root_task_and_returns_persisted_receipt(runtime, note):
    service, repo, cid = runtime
    assert isinstance(service, ContextServiceProtocol)
    assert WorkStartCommand is CanonicalWorkStartCommand
    # A Message advances Collet only: neither fence may substitute for the other.
    _append(
        runtime,
        "user_interaction.posted",
        OWNER,
        primitive="Message",
        payload={"content": "补充交付要求"},
    )
    before = repo.get_current_status(cid)
    assert before.collet.version == 2 and before.task.version == 1
    result = service.start(cid, WorkStartCommand(2, note), BOT)
    after = repo.get_current_status(cid)
    event = after.events[-1]
    assert result.accepted and result.current_status == "in_progress"
    assert result.collab_context_id == cid
    assert result.event_kind == event.event_kind == "task.started"
    assert result.primitive_type == event.primitive_type == "Task"
    assert result.context_version == after.collet.version == 3
    assert result.event_id == after.collet.current_event_id == event.event_id
    assert result.parent_event == event.parent_event
    assert after.task.task_id == before.task.task_id != cid
    assert after.task.version == 2 and after.task.status == "in_progress"
    assert after.task.last_event_ref == event.event_id
    assert (event.task_ref, event.task_version_after) == (after.task.task_id, 2)
    assert event.actor_ref == BOT.actor and event.visibility == "public"
    assert event.payload == ({"note": note} if note else {})
    assert after.product_instance == before.product_instance
    assert len(after.events) == len(before.events) + 1  # No Activity/dispatch event.


@pytest.mark.parametrize(
    "actor",
    [
        OWNER,
        ActorContext(PrincipalRef("bot", "stranger"), "a2a"),
        ActorContext(PrincipalRef("human", "worker"), "api"),
    ],
)
def test_start_keeps_authorization_and_typed_executor_role(runtime, actor):
    service, _, cid = runtime
    _reject_unchanged(runtime, lambda: service.start(cid, WorkStartCommand(1), actor))


def test_start_rejects_missing_context(runtime):
    service, _, _ = runtime
    with pytest.raises(ContextCollabNotFoundError):
        service.start("collet_missing", WorkStartCommand(1), BOT)


def test_stale_and_repeated_start_do_not_append_or_advance(runtime):
    service, _, cid = runtime
    started = service.start(cid, WorkStartCommand(1), BOT)
    _reject_unchanged(
        runtime,
        lambda: service.start(cid, WorkStartCommand(1), BOT),
        ContextCollabVersionConflict,
    )
    _reject_unchanged(
        runtime,
        lambda: service.start(cid, WorkStartCommand(started.context_version), BOT),
    )


@pytest.mark.parametrize("terminal", ["done", "cancelled"])
def test_start_does_not_reopen_terminal_task(runtime, terminal):
    service, repo, cid = runtime
    if terminal == "done":
        service.start(cid, WorkStartCommand(1), BOT)
        _append(runtime, "task.submitted", payload={"artifacts": ["artifact://result"]})
        _append(runtime, "task.accepted", OWNER)
    else:
        _append(runtime, "task.cancelled", OWNER)
    current = repo.get_current_status(cid)
    assert current.task.status == terminal
    _reject_unchanged(
        runtime,
        lambda: service.start(cid, WorkStartCommand(current.collet.version), BOT),
    )


def test_start_uses_pinned_machine_not_unversioned_registry(runtime, monkeypatch):
    service, _, cid = runtime
    monkeypatch.setattr(workflow_resolution, "MACHINE_REGISTRY", {})
    assert service.start(cid, WorkStartCommand(1), BOT).current_status == "in_progress"


def test_start_rejects_unavailable_exact_machine(runtime, monkeypatch):
    service, _, cid = runtime
    monkeypatch.setattr(workflow_resolution, "VERSIONED_MACHINE_REGISTRY", {})
    _reject_unchanged(runtime, lambda: service.start(cid, WorkStartCommand(1), BOT))


@pytest.mark.parametrize(
    "table", ["ac_context_collab_task", "ac_context_collab_collet"]
)
def test_start_sql_cas_conflict_rolls_back_all_authority(runtime, engine, table):
    service, _, cid = runtime

    def conflict(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"UPDATE {table} "):
            return statement + " AND 0 = 1", parameters
        return statement, parameters

    sql_event.listen(engine, "before_cursor_execute", conflict, retval=True)
    try:
        _reject_unchanged(
            runtime,
            lambda: service.start(cid, WorkStartCommand(1), BOT),
            ContextCollabVersionConflict,
        )
    finally:
        sql_event.remove(engine, "before_cursor_execute", conflict)


@pytest.mark.parametrize(
    "statement_prefix",
    [
        "UPDATE ac_context_collab_task",
        "UPDATE ac_context_collab_collet",
        "INSERT INTO ac_context_collab_event",
    ],
)
def test_start_write_failure_is_not_success_and_rolls_back(
    runtime, engine, statement_prefix
):
    service, _, cid = runtime

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(statement_prefix):
            raise RuntimeError("injected SQL write failure")

    sql_event.listen(engine, "before_cursor_execute", fail)
    try:
        _reject_unchanged(
            runtime, lambda: service.start(cid, WorkStartCommand(1), BOT), RuntimeError
        )
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail)


@pytest.mark.parametrize(
    "product,workflow,method,case",
    [
        ("team_todo", "team_todo_execution", "Single", "repeated"),
        ("public_discussion", "forum_public_discussion", "Open + Group", "no_machine"),
    ],
)
def test_explicit_start_is_strict_even_when_generic_append_is_permissive(
    db, product, workflow, method, case
):
    catalog = ProductCatalog(LEGACY_PRODUCT_PROFILES)
    repo = ContextCollabRepository(db, product_catalog=catalog)
    composer = ApplicationCollabService(
        repo, InMemoryMethodStore(), product_catalog=catalog
    )
    collet = composer.create_collaboration(
        CollaborationCreateCommand(
            to_space=SpaceRef("space", "test"),
            principal_ref=OWNER.actor,
            product_kind=product,
            workflow_kind=workflow,
            method_kind=method,
            executor_ref=AgentRef("bot", "worker"),
            objective="legacy work",
            resource_refs=[],
        ),
        OWNER,
    )
    authorizer = StructuralContextCollabAuthorizer()
    service = ContextService(
        repo, ProductCollaborationPolicy(catalog, authorizer), authorizer
    )
    runtime = service, repo, collet.collet_id
    if case == "repeated":
        service.start(collet.collet_id, WorkStartCommand(1), BOT)
    current = repo.get_current_status(collet.collet_id)
    _reject_unchanged(
        runtime,
        lambda: service.start(
            collet.collet_id, WorkStartCommand(current.collet.version), BOT
        ),
    )
    # The generic compatibility API still accepts unmatched/no-machine events.
    result = _append(runtime, "task.started")
    assert result.accepted and result.current_status == current.task.status
