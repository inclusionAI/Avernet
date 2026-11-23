from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest

from agentclaw.community.core.repository.implementations.task.todos.todo_template_repository import (
    TodoTemplateRepository,
)
from agentclaw.community.core.task.todos.errors import (
    TodoTemplateNotFoundError,
    TodoTemplateStateConflictError,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateStatus,
    TodoTemplateVersionStatus,
)
from agentclaw.community.core.task.todos.template_models import (
    TodoCompletionGate,
    TodoParticipantDefinition,
    TodoParticipantReference,
    TodoParticipantRefSource,
    TodoParticipantRequiredAt,
    TodoParticipantSubjectType,
    TodoStageDefinition,
    TodoStageKind,
    TodoTemplateDefinition,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def _definition(name: str = "Standard delivery") -> TodoTemplateDefinition:
    return TodoTemplateDefinition(
        schema_version="1.0",
        name=name,
        description="Implement and verify a Todo.",
        binding_schema=(
            TodoParticipantDefinition(
                key="developer_bot",
                label="Developer Bot",
                subject_type=TodoParticipantSubjectType.BOT,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
            TodoParticipantDefinition(
                key="technical_owner",
                label="Technical Owner",
                subject_type=TodoParticipantSubjectType.HUMAN,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
        ),
        stages=(
            TodoStageDefinition(
                key="development",
                name="Development",
                purpose="Implement the Todo.",
                stage_kind=TodoStageKind.BOT,
                executor_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="developer_bot",
                ),
                owner_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="technical_owner",
                ),
                completion_gate=TodoCompletionGate(),
            ),
        ),
    )


def _create(repo: TodoTemplateRepository, **overrides):
    values = {
        "space_id": 7,
        "definition": _definition(),
        "created_by": "user_1",
    }
    values.update(overrides)
    return repo.create_template(**values)


def test_create_template_and_first_draft_are_atomic_and_hash_is_persisted(db) -> None:
    repo = TodoTemplateRepository(db)

    detail = _create(repo)

    assert detail.template.template_id.startswith("todo_tpl_")
    assert detail.template.status is TodoTemplateStatus.ACTIVE
    assert detail.template.published_version_id is None
    assert detail.template.name == "Standard delivery"
    assert len(detail.versions) == 1
    draft = detail.versions[0]
    assert draft.template_version_id.startswith("todo_tplv_")
    assert draft.version_no == 1
    assert draft.status is TodoTemplateVersionStatus.DRAFT
    assert draft.definition == _definition()
    assert len(draft.definition_hash) == 64
    assert repo.get_template(template_id=detail.template.template_id) == detail.template
    assert (
        repo.get_version(
            template_id=detail.template.template_id,
            template_version_id=draft.template_version_id,
        )
        == draft
    )


def test_publish_is_immutable_updates_pointer_and_next_draft_copies_definition(
    db,
) -> None:
    repo = TodoTemplateRepository(db)
    detail = _create(repo)
    draft = detail.versions[0]

    published = repo.publish_version(
        template_id=detail.template.template_id,
        template_version_id=draft.template_version_id,
        published_by="admin_1",
    )

    assert published.status is TodoTemplateVersionStatus.PUBLISHED
    assert published.published_by == "admin_1"
    assert published.published_at is not None
    assert (
        repo.get_template(template_id=detail.template.template_id).published_version_id
        == published.template_version_id
    )
    with pytest.raises(TodoTemplateStateConflictError, match="published.*immutable"):
        repo.update_draft(
            template_id=detail.template.template_id,
            template_version_id=published.template_version_id,
            definition=replace(_definition(), name="Changed in place"),
            updated_by="admin_1",
        )

    next_draft = repo.create_draft(
        template_id=detail.template.template_id,
        created_by="admin_1",
    )
    assert next_draft.version_no == 2
    assert next_draft.status is TodoTemplateVersionStatus.DRAFT
    assert next_draft.definition == published.definition
    assert next_draft.definition_hash == published.definition_hash

    changed = replace(next_draft.definition, name="Delivery V2")
    updated = repo.update_draft(
        template_id=detail.template.template_id,
        template_version_id=next_draft.template_version_id,
        definition=changed,
        updated_by="admin_1",
    )
    assert updated.definition.name == "Delivery V2"
    assert (
        repo.get_template(template_id=detail.template.template_id).name == "Delivery V2"
    )
    assert published.definition.name == "Standard delivery"


def test_only_one_draft_exists_and_republishing_current_version_is_idempotent(
    db,
) -> None:
    repo = TodoTemplateRepository(db)
    detail = _create(repo)
    draft = detail.versions[0]

    with pytest.raises(TodoTemplateStateConflictError, match="draft already exists"):
        repo.create_draft(
            template_id=detail.template.template_id,
            created_by="admin_1",
        )

    first = repo.publish_version(
        template_id=detail.template.template_id,
        template_version_id=draft.template_version_id,
        published_by="admin_1",
    )
    replay = repo.publish_version(
        template_id=detail.template.template_id,
        template_version_id=draft.template_version_id,
        published_by="admin_1",
    )
    assert replay == first


def test_list_is_space_scoped_and_archive_activate_preserve_versions(db) -> None:
    repo = TodoTemplateRepository(db)
    first = _create(repo, space_id=7)
    _create(repo, space_id=8, definition=_definition("Other Space"))

    page = repo.list_templates(
        space_id=7,
        status=TodoTemplateStatus.ACTIVE,
        page=1,
        page_size=20,
    )
    assert page.total == 1
    assert page.items == (first.template,)

    archived = repo.set_template_status(
        template_id=first.template.template_id,
        status=TodoTemplateStatus.ARCHIVED,
        updated_by="admin_1",
    )
    assert archived.status is TodoTemplateStatus.ARCHIVED
    assert len(repo.list_versions(template_id=first.template.template_id)) == 1

    active = repo.set_template_status(
        template_id=first.template.template_id,
        status=TodoTemplateStatus.ACTIVE,
        updated_by="admin_1",
    )
    assert active.status is TodoTemplateStatus.ACTIVE


def test_repository_isolates_tenant_and_environment(db, monkeypatch) -> None:
    repo = TodoTemplateRepository(db)
    monkeypatch.setenv("SERVER_ENV", "pre")
    with avernet_tenant_scope("tenant-a"):
        detail = _create(repo)

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(TodoTemplateNotFoundError):
            repo.get_template(template_id=detail.template.template_id)
        assert (
            repo.list_templates(
                space_id=7,
                status=None,
                page=1,
                page_size=20,
            ).total
            == 0
        )

    with avernet_tenant_scope("tenant-a"):
        monkeypatch.setenv("SERVER_ENV", "prod")
        with pytest.raises(TodoTemplateNotFoundError):
            repo.get_template(template_id=detail.template.template_id)


def test_version_address_requires_matching_template(db) -> None:
    repo = TodoTemplateRepository(db)
    first = _create(repo)
    second = _create(repo, definition=_definition("Second"))

    with pytest.raises(TodoTemplateNotFoundError):
        repo.get_version(
            template_id=second.template.template_id,
            template_version_id=first.versions[0].template_version_id,
        )


def test_database_write_failure_is_not_swallowed(db) -> None:
    repo = TodoTemplateRepository(db)

    class BrokenSession:
        def add(self, _record):
            raise RuntimeError("write failed")

    @contextmanager
    def broken_transaction():
        yield BrokenSession()

    repo._transaction = broken_transaction  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="write failed"):
        _create(repo)
