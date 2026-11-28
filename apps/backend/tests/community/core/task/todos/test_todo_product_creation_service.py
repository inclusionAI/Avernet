from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDetail,
    BotTeamRecord,
    BotTeamRevisionRecord,
    BotTeamStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoSourceAlreadyImportedError,
    TodoTemplateStateConflictError,
    TodoTemplateValidationError,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.product_creation_models import (
    TodoDimaImportItem,
    TodoDimaImportStatus,
    TodoExecutorKind,
    TodoExecutorSelection,
    TodoOwnerInput,
    TodoProductColletStatus,
)
from agentclaw.community.core.task.todos.services.todo_product_creation_service import (
    TodoProductCreationService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.todos.source_models import TodoSourceInput

_NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _todo(**overrides: Any) -> TodoRecord:
    values: dict[str, Any] = {
        "todo_id": "todo_1",
        "space_id": 7,
        "title": "Implement feature",
        "description": "Ship it",
        "bot_id": "bot_1",
        "bot_name": "Coding Bot",
        "status": TodoStatus.NOT_STARTED,
        "collab_context_id": None,
        "created_by": "creator_1",
        "created_at": _NOW,
        "updated_at": _NOW,
        "owner_id": "owner_1",
        "owner_name": "Owner One",
    }
    values.update(overrides)
    return TodoRecord(**values)


def _detail(
    *,
    space_id: int = 7,
    status: BotTeamStatus = BotTeamStatus.ACTIVE,
    revision_ids: tuple[str, ...] = ("todo_tplv_1",),
    product_profile_id: int | None = None,
) -> BotTeamDetail:
    team = BotTeamRecord(
        bot_team_id="todo_tpl_1",
        space_id=space_id,
        name="Coding delivery",
        description="Analyze, implement, verify",
        status=status,
        published_revision_id=revision_ids[-1] if revision_ids else None,
        created_by="admin_1",
        updated_by="admin_1",
        created_at=_NOW,
        updated_at=_NOW,
    )
    revisions = tuple(
        BotTeamRevisionRecord(
            bot_team_revision_id=revision_id,
            bot_team_id=team.bot_team_id,
            revision_no=index,
            name=team.name,
            description=team.description,
            nodes=(),
            definition_hash=str(index) * 64,
            created_by="admin_1",
            published_by="admin_1",
            published_at=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
            product_profile_id=product_profile_id,
        )
        for index, revision_id in enumerate(revision_ids, start=1)
    )
    return BotTeamDetail(bot_team=team, revisions=revisions)


class _Todos:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_todo(self, **kwargs: Any) -> TodoRecord:
        self.calls.append(kwargs)
        owner = kwargs["owner"]
        return _todo(owner_id=owner.user_id, owner_name=owner.user_name)


class _BotTeams:
    def __init__(self, detail: BotTeamDetail | None = None) -> None:
        self.detail = detail or _detail()
        self.calls: list[dict[str, Any]] = []

    def get_bot_team(self, **kwargs: Any) -> BotTeamDetail:
        self.calls.append(kwargs)
        return self.detail


class _TemplateCreation:
    def __init__(
        self,
        *,
        duplicate_source_item_ids: frozenset[str] = frozenset(),
        failing_source_item_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.duplicate_source_item_ids = duplicate_source_item_ids
        self.failing_source_item_ids = failing_source_item_ids

    def create_todo_from_template(self, **kwargs: Any) -> TodoRecord:
        self.calls.append(kwargs)
        source = kwargs.get("source")
        if source is not None and source.item_id in self.duplicate_source_item_ids:
            raise TodoSourceAlreadyImportedError("todo_existing")
        if source is not None and source.item_id in self.failing_source_item_ids:
            raise RuntimeError("template creation failed")
        owner = kwargs["bindings"]["todo_owner"]
        return _todo(
            bot_id="bot_requirement",
            template_id=kwargs["template_id"],
            template_version_id=kwargs["template_version_id"],
            template_name_snapshot="Coding delivery",
            owner_id=owner.subject_id,
            owner_name=owner.subject_name or owner.subject_id,
        )


class _ProductProfiles:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure

    async def create_product_collet(self, *args: Any, **kwargs: Any) -> Collet:
        if self.failure is not None:
            raise self.failure
        return Collet(
            id=22,
            profile_id=args[0],
            content=args[1],
            status=StatusEnum.CREATE,
        )


class _Repository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_todo(self, **kwargs: Any) -> TodoRecord:
        self.calls.append({"operation": "get_todo", **kwargs})
        return _todo(
            template_id="todo_tpl_1",
            template_version_id="todo_tplv_1",
            template_name_snapshot="Coding delivery",
        )

    def list_stages(self, **kwargs: Any) -> tuple[()]:
        self.calls.append({"operation": "list_stages", **kwargs})
        return ()

    def get_source_binding(self, **kwargs: Any) -> None:
        self.calls.append({"operation": "get_source_binding", **kwargs})
        return None

    def bind_product_collet(self, **kwargs: Any) -> TodoRecord:
        self.calls.append({"operation": "bind_product_collet", **kwargs})
        return _todo(product_collet_id=kwargs["product_collet_id"])


class _SingleBotRepository(_Repository):
    def get_todo(self, **kwargs: Any) -> TodoRecord:
        self.calls.append({"operation": "get_todo", **kwargs})
        return _todo()


def _service(
    *,
    detail: BotTeamDetail | None = None,
    templates: _TemplateCreation | None = None,
    product_profiles: _ProductProfiles | None = None,
):
    todos = _Todos()
    bot_teams = _BotTeams(detail)
    templates = templates or _TemplateCreation()
    repository = _Repository()
    return (
        TodoProductCreationService(
            todos=todos,
            bot_teams=bot_teams,
            template_creation=templates,
            product_profiles=product_profiles or _ProductProfiles(),
            repository=repository,
        ),
        todos,
        bot_teams,
        templates,
    )


def test_single_bot_executor_delegates_with_owner_and_source() -> None:
    service, todos, bot_teams, templates = _service()
    owner = TodoOwnerInput(user_id=" owner_1 ", user_name=" Owner One ")
    source = TodoSourceInput(
        source_type="dima",
        container_id="P1",
        item_id="work_1",
    )

    created = service.create_todo(
        space_id=7,
        actor_id="creator_1",
        title=" Implement feature ",
        description=" Ship it ",
        executor=TodoExecutorSelection(
            type=TodoExecutorKind.BOT,
            id=" bot_1 ",
        ),
        owner=owner,
        source=source,
        env="pre",
    )

    assert created.owner_id == "owner_1"
    assert created.owner_name == "Owner One"
    assert todos.calls == [
        {
            "space_id": 7,
            "actor_id": "creator_1",
            "title": "Implement feature",
            "description": "Ship it",
            "bot_id": "bot_1",
            "owner": TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
            "source": source,
            "env": "pre",
        }
    ]
    assert bot_teams.calls == []
    assert templates.calls == []


def test_bot_team_executor_maps_product_ids_to_existing_template_creation() -> None:
    service, todos, bot_teams, templates = _service()

    created = service.create_todo(
        space_id=7,
        actor_id="creator_1",
        title="Implement feature",
        description="Ship it",
        executor=TodoExecutorSelection(
            type=TodoExecutorKind.BOT_TEAM,
            id="todo_tpl_1",
            revision_id="todo_tplv_1",
        ),
        owner=TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
        env="pre",
    )

    assert created.template_id == "todo_tpl_1"
    assert created.template_version_id == "todo_tplv_1"
    assert created.template_name_snapshot == "Coding delivery"
    assert created.owner_id == "owner_1"
    assert bot_teams.calls == [
        {"bot_team_id": "todo_tpl_1", "actor_id": "creator_1", "env": "pre"}
    ]
    assert templates.calls == [
        {
            "space_id": 7,
            "actor_id": "creator_1",
            "template_id": "todo_tpl_1",
            "template_version_id": "todo_tplv_1",
            "title": "Implement feature",
            "description": "Ship it",
            "bindings": templates.calls[0]["bindings"],
            "source": None,
            "env": "pre",
        }
    ]
    binding = templates.calls[0]["bindings"]["todo_owner"]
    assert binding.subject_type == "human"
    assert binding.subject_id == "owner_1"
    assert binding.subject_name == "Owner One"
    assert todos.calls == []


@pytest.mark.parametrize(
    ("detail", "revision_id", "error", "match"),
    [
        (
            _detail(space_id=8),
            "todo_tplv_1",
            TodoTemplateValidationError,
            "space",
        ),
        (
            _detail(status=BotTeamStatus.ARCHIVED),
            "todo_tplv_1",
            TodoTemplateStateConflictError,
            "archived",
        ),
        (
            _detail(revision_ids=("todo_tplv_2",)),
            "todo_tplv_1",
            TodoTemplateValidationError,
            "revision",
        ),
    ],
)
def test_bot_team_executor_rejects_incompatible_team_snapshot(
    detail: BotTeamDetail,
    revision_id: str,
    error: type[Exception],
    match: str,
) -> None:
    service, todos, _bot_teams, templates = _service(detail=detail)

    with pytest.raises(error, match=match):
        service.create_todo(
            space_id=7,
            actor_id="creator_1",
            title="Implement feature",
            description="Ship it",
            executor=TodoExecutorSelection(
                type=TodoExecutorKind.BOT_TEAM,
                id="todo_tpl_1",
                revision_id=revision_id,
            ),
            owner=TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
        )

    assert todos.calls == []
    assert templates.calls == []


@pytest.mark.parametrize(
    "executor",
    [
        TodoExecutorSelection(
            type=TodoExecutorKind.BOT,
            id="bot_1",
            revision_id="unexpected",
        ),
        TodoExecutorSelection(
            type=TodoExecutorKind.BOT_TEAM,
            id="todo_tpl_1",
            revision_id=None,
        ),
    ],
)
def test_executor_revision_shape_is_strict(executor: TodoExecutorSelection) -> None:
    service, todos, bot_teams, templates = _service()

    with pytest.raises(ValidationError, match="revision_id"):
        service.create_todo(
            space_id=7,
            actor_id="creator_1",
            title="Implement feature",
            description="Ship it",
            executor=executor,
            owner=TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
        )

    assert todos.calls == []
    assert bot_teams.calls == []
    assert templates.calls == []


def _import_item(item_id: str, *, title: str = "Dima item") -> TodoDimaImportItem:
    return TodoDimaImportItem(
        title=title,
        description="Imported Dima work item",
        source=TodoSourceInput(
            source_type="dima",
            container_id="P1",
            item_id=item_id,
        ),
    )


@pytest.mark.asyncio
async def test_dima_import_sizes_and_normalizes_before_touching_dependencies() -> None:
    service, todos, _bot_teams, templates = _service()

    with pytest.raises(ValidationError, match="between 1 and 20"):
        await service.import_dima_todos(
            space_id=7,
            actor_id="creator_1",
            executor=TodoExecutorSelection(
                type=TodoExecutorKind.BOT_TEAM,
                id="todo_tpl_1",
                revision_id="todo_tplv_1",
            ),
            owner=TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
            items=tuple(_import_item(f"DIM-{index}") for index in range(21)),
        )

    assert todos.calls == []
    assert templates.calls == []


@pytest.mark.asyncio
async def test_dima_import_returns_independent_results_for_each_item() -> None:
    templates = _TemplateCreation(
        duplicate_source_item_ids=frozenset({"DIM-DUPLICATE"}),
        failing_source_item_ids=frozenset({"DIM-BROKEN"}),
    )
    service, _todos, _bot_teams, templates = _service(
        detail=_detail(product_profile_id=11),
        templates=templates,
    )

    results = await service.import_dima_todos(
        space_id=7,
        actor_id="creator_1",
        executor=TodoExecutorSelection(
            type=TodoExecutorKind.BOT_TEAM,
            id="todo_tpl_1",
            revision_id="todo_tplv_1",
        ),
        owner=TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
        items=(
            _import_item("DIM-NEW"),
            _import_item("DIM-DUPLICATE"),
            _import_item("DIM-BROKEN"),
        ),
    )

    assert [result.status.value for result in results] == [
        "imported",
        "duplicate",
        "failed",
    ]
    assert results[0].todo_id == "todo_1"
    assert results[0].product_runtime_status is TodoProductColletStatus.READY
    assert results[1].todo_id == "todo_existing"
    assert results[1].product_runtime_status is TodoProductColletStatus.READY
    assert results[2].todo_id is None
    assert (
        results[2].product_runtime_status is TodoProductColletStatus.RETRYABLE_FAILURE
    )
    assert results[2].error_code == "dima_import_failed"
    assert results[2].retryable is True
    assert len(templates.calls) == 3


@pytest.mark.asyncio
async def test_product_collet_failure_preserves_imported_todo_as_retryable_failure() -> (
    None
):
    templates = _TemplateCreation()
    profiles = _ProductProfiles(failure=RuntimeError("protocol unavailable"))
    detail = _detail(product_profile_id=11)
    service, _todos, _bot_teams, templates = _service(
        detail=detail,
        templates=templates,
        product_profiles=profiles,
    )

    results = await service.import_dima_todos(
        space_id=7,
        actor_id="creator_1",
        executor=TodoExecutorSelection(
            type=TodoExecutorKind.BOT_TEAM,
            id="todo_tpl_1",
            revision_id="todo_tplv_1",
        ),
        owner=TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
        items=(_import_item("DIM-NEW"),),
    )

    assert len(results) == 1
    assert results[0].status.value == "failed"
    assert results[0].todo_id == "todo_1"
    assert (
        results[0].product_runtime_status is TodoProductColletStatus.RETRYABLE_FAILURE
    )
    assert results[0].error_code == "product_runtime_unavailable"
    assert results[0].retryable is True
    assert len(templates.calls) == 1


@pytest.mark.asyncio
async def test_single_bot_dima_import_succeeds_without_product_collet() -> None:
    todos = _Todos()
    service = TodoProductCreationService(
        todos=todos,
        bot_teams=_BotTeams(),
        template_creation=_TemplateCreation(),
        product_profiles=_ProductProfiles(),
        repository=_SingleBotRepository(),
    )

    results = await service.import_dima_todos(
        space_id=7,
        actor_id="creator_1",
        executor=TodoExecutorSelection(
            type=TodoExecutorKind.BOT,
            id="bot_1",
        ),
        owner=TodoOwnerInput(user_id="owner_1", user_name="Owner One"),
        items=(_import_item("DIM-SINGLE-BOT"),),
    )

    assert len(results) == 1
    assert results[0].status is TodoDimaImportStatus.IMPORTED
    assert results[0].todo_id == "todo_1"
    assert results[0].product_runtime_status is TodoProductColletStatus.NOT_APPLICABLE
    assert results[0].error_code is None
    assert results[0].retryable is False
    assert todos.calls[0]["bot_id"] == "bot_1"
