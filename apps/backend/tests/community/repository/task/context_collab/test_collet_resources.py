"""Explicit L4 creation preserves typed resources without Work or access grants."""

import json
from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
    ColletCreateCommand,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    PrimitiveRecordDecoder,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    ResourceBinding,
)

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
RESOURCES = (
    ResourceBinding(ResourceRef("document", "需求说明.md", "v1")),
    ResourceBinding(ResourceRef("repository", "calculator", None)),
)


@pytest.fixture
def runtime(db):
    repository = ContextCollabRepository(db, PrimitiveRecordDecoder())
    return ContextService(repository, repository), repository


def _command(**kwargs):
    return ColletCreateCommand(OWNER.actor, **kwargs)


def test_create_resources_persist_without_creating_work_or_binding_events(runtime, db):
    service, repository = runtime
    command = _command(
        objective="实现计算器",
        acceptance_criteria="求和测试通过",
        resources=RESOURCES,
    )
    collet = service.create_collet(command, OWNER)
    record = repository.get_current_status(collet.collet_id)
    assert record.collet == collet
    assert collet.resources == RESOURCES
    assert collet.metadata.objective == command.objective
    assert collet.metadata.acceptance_criteria == command.acceptance_criteria
    assert collet.etag != replace(collet, resources=()).etag
    assert record.task is None and record.tasks == () and record.events == []
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert json.loads(row.resources) == [item.to_dict() for item in RESOURCES]
        assert json.loads(row.creation_facts) == {}
        assert row.root_task_ref is None
        assert session.query(ContextCollabTaskModel).count() == 0
        assert session.query(ContextCollabEventModel).count() == 0

    task = service.create_task(
        collet.collet_id, TaskCreateCommand(1, OWNER.actor, "独立任务目标"), OWNER
    )
    record = repository.get_current_status(collet.collet_id)
    assert record.collet.resources == RESOURCES
    assert task.objective == "独立任务目标" and task.status == "created"
    assert record.events[0].payload == {
        "objective": task.objective,
        "executor_refs": [],
    }
    repository.append_event(
        collet.collet_id,
        AppendEventRecordRequest(
            2,
            "user_interaction.posted",
            "Message",
            OWNER.actor,
            None,
            "public",
            {"content": "进度更新"},
        ),
    )
    assert repository.get_current_status(collet.collet_id).collet.resources == RESOURCES


@pytest.mark.parametrize(
    "resources",
    [
        None,
        "resource",
        [],
        ({},),
        ("resource",),
        (ResourceRef("document", "one", None),),
        (ResourceBinding(None),),
        (ResourceBinding("bad"),),
        (ResourceBinding(ResourceRef("document", "", None)),),
        (ResourceBinding(ResourceRef("document", "one", 1)),),
    ],
)
def test_create_command_rejects_invalid_resources(resources):
    with pytest.raises(ContextCollabValidationError):
        _command(resources=resources)


def test_legacy_null_resources_read_as_empty_without_rewrite(runtime, db):
    service, repository = runtime
    collet = service.create_collet(_command(), OWNER)
    with db.orm_session() as session:
        session.query(ContextCollabColletModel).one().resources = None
    assert repository.get_current_status(collet.collet_id).collet.resources == ()
    with db.orm_session() as session:
        assert session.query(ContextCollabColletModel).one().resources is None


@pytest.mark.parametrize(
    "stored", ["{bad", "null", "{}", '"resource"', "[null]", "[{}]"]
)
def test_invalid_stored_resources_fail_read_and_append_without_writes(
    runtime, db, stored
):
    service, repository = runtime
    collet = service.create_collet(_command(), OWNER)
    with db.orm_session() as session:
        session.query(ContextCollabColletModel).one().resources = stored
    with pytest.raises(ContextCollabValidationError, match="resources"):
        repository.get_current_status(collet.collet_id)
    with pytest.raises(ContextCollabValidationError, match="resources"):
        repository.append_event(
            collet.collet_id,
            AppendEventRecordRequest(
                1,
                "user_interaction.posted",
                "Message",
                OWNER.actor,
                None,
                "public",
                {"content": "进度更新"},
            ),
        )
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert row.resources == stored and row.context_version == 1
        assert session.query(ContextCollabEventModel).count() == 0
