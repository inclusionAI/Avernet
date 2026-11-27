from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.todos.product_creation_models import (
    build_collet_request_id,
    build_product_collet_content,
    resolve_todo_runtime_kind,
    TodoProductColletStatus,
    TodoRuntimeKind,
)
from agentclaw.community.core.task.todos.errors import TodoStateConflictError
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.source_models import TodoSourceInput
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.core.task.todos.stage_models import (
    TodoStageRecord,
    TodoStageStatus,
)

NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


def _todo(**overrides: object) -> TodoRecord:
    values: dict[str, object] = {
        "todo_id": "todo_1",
        "space_id": 7,
        "title": "Implement Todo",
        "description": "Implement and verify",
        "bot_id": "bot_1",
        "bot_name": "Coding Bot",
        "status": TodoStatus.NOT_STARTED,
        "collab_context_id": None,
        "created_by": "creator_1",
        "created_at": NOW,
        "updated_at": NOW,
        "template_id": "todo_tpl_1",
        "template_version_id": "todo_tplv_1",
        "template_name_snapshot": "Coding delivery",
        "product_collet_id": None,
    }
    values.update(overrides)
    return TodoRecord(**values)  # type: ignore[arg-type]


def _stage(order: int = 0) -> TodoStageRecord:
    return TodoStageRecord(
        stage_instance_id=f"stage_{order}",
        todo_id="todo_1",
        template_version_id="todo_tplv_1",
        stage_key=f"stage_{order}",
        stage_order=order,
        stage_name=f"Stage {order}",
        purpose=f"Purpose {order}",
        stage_kind="bot",
        executor_binding_key="executor",
        executor_subject_type="bot",
        executor_subject_id="bot_1",
        executor_subject_name="Coding Bot",
        owner_binding_key="owner",
        owner_user_id="owner_1",
        owner_user_name="Owner One",
        completion_gate_json='{"required_artifacts":[]}',
        status=TodoStageStatus.NOT_STARTED,
        collab_context_id=None,
        pending_action_kind=None,
        state_reason=None,
        requested_action=None,
        started_at=None,
        completed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("profile_id", "collet_id", "context_id", "expected"),
    [
        (1, 1, "context_1", TodoRuntimeKind.PRODUCT_COLLET),
        (1, None, None, TodoRuntimeKind.PRODUCT_RUNTIME_PENDING),
        (None, None, "context_1", TodoRuntimeKind.LEGACY_CONTEXT),
        (None, None, None, TodoRuntimeKind.LEGACY_CONTEXT),
        (None, 1, None, TodoRuntimeKind.INVALID_BINDING),
        (1, None, "context_1", TodoRuntimeKind.INVALID_BINDING),
    ],
)
def test_runtime_kind_resolver_is_the_complete_truth_table(
    profile_id: int | None,
    collet_id: int | None,
    context_id: str | None,
    expected: TodoRuntimeKind,
) -> None:
    assert (
        resolve_todo_runtime_kind(
            _todo(product_collet_id=collet_id, collab_context_id=context_id),
            product_profile_id=profile_id,
        )
        is expected
    )


def test_collet_request_id_is_stable_per_todo_environment_and_tenant() -> None:
    expected = "todo:collet:tenant-a:pre:todo_1:v1"

    assert (
        build_collet_request_id("tenant-a", "pre", "todo_1")
        == build_collet_request_id("tenant-a", "pre", "todo_1")
        == expected
    )
    assert build_collet_request_id("tenant-b", "pre", "todo_1") != expected
    assert build_collet_request_id("tenant-a", "prod", "todo_1") != expected
    assert build_collet_request_id("tenant-a", "pre", "todo_2") != expected


def test_product_collet_content_is_stable_and_contains_product_facts() -> None:
    source = TodoSourceInput(
        source_type="dima",
        container_id="P1",
        item_id="DIM-1",
        url="https://dima.example/DIM-1",
        updated_at="2026-11-27T00:00:00Z",
        metadata={"processor_name": "Owner One"},
    )

    first = build_product_collet_content(
        todo=_todo(),
        source=source,
        stages=(_stage(0), _stage(1)),
        product_profile_id=11,
    )
    second = build_product_collet_content(
        todo=_todo(),
        source=source,
        stages=(_stage(0), _stage(1)),
        product_profile_id=11,
    )

    assert first == second
    assert "Implement Todo" in first
    assert "Implement and verify" in first
    assert "Coding delivery" in first
    assert "DIM-1" in first
    assert "Stage 0" in first
    assert "Stage 1" in first
    assert "Owner One" in first
    assert "11" in first


class TestEnsureProductCollet:
    def test_protocol_result_models_correctly(self) -> None:
        from agentclaw.community.core.task.todos.product_creation_models import (
            EnsureProductColletResult,
        )

        result = EnsureProductColletResult(
            status=TodoProductColletStatus.READY,
            todo=replace(_todo(product_collet_id=22)),
        )

        assert result.todo.product_collet_id == 22
        assert result.status is TodoProductColletStatus.READY


class RecordingProductProfiles:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.failure = failure

    async def create_product_collet(
        self,
        profile_id: int,
        content: str,
        *,
        context: ActorContext,
        request_id: str,
    ) -> Collet:
        self.calls.append(
            {
                "profile_id": profile_id,
                "content": content,
                "context": context,
                "request_id": request_id,
            }
        )
        if self.failure is not None:
            raise self.failure
        return Collet(
            id=22,
            profile_id=profile_id,
            content=content,
            status=StatusEnum.CREATE,
        )


def _pending_runtime_repository(*, binding_conflict: bool = False):
    class Repository:
        def __init__(self) -> None:
            self.calls: list[object] = []
            self.binding_conflict = binding_conflict

        def get_todo(self, **kwargs: object) -> TodoRecord:
            return _todo(product_collet_id=None)

        def list_stages(self, **kwargs: object) -> tuple[TodoStageRecord, ...]:
            return (_stage(0),)

        def get_source_binding(self, **kwargs: object) -> TodoSourceInput | None:
            return TodoSourceInput(
                source_type="dima",
                container_id="P1",
                item_id="DIM-1",
            )

        def bind_product_collet(self, **kwargs: object) -> TodoRecord:
            self.calls.append(kwargs)
            if self.binding_conflict:
                raise TodoStateConflictError("different Product Collet")
            return _todo(product_collet_id=kwargs["product_collet_id"])

    return Repository()


def _service_for_pending_todo(profiles: RecordingProductProfiles, repository: object):
    from agentclaw.community.core.task.todos.bot_team_models import (
        BotTeamDetail,
        BotTeamRecord,
        BotTeamRevisionRecord,
        BotTeamStatus,
    )
    from agentclaw.community.core.task.todos.services.todo_product_creation_service import (
        TodoProductCreationService,
    )

    bot_team = BotTeamRecord(
        bot_team_id="todo_tpl_1",
        space_id=7,
        name="Coding delivery",
        description="Analyze, implement, verify",
        status=BotTeamStatus.ACTIVE,
        published_revision_id="todo_tplv_1",
        created_by="admin_1",
        updated_by="admin_1",
        created_at=NOW,
        updated_at=NOW,
    )
    revision = BotTeamRevisionRecord(
        bot_team_revision_id="todo_tplv_1",
        bot_team_id="todo_tpl_1",
        revision_no=1,
        name=bot_team.name,
        description=bot_team.description,
        nodes=(),
        definition_hash="1" * 64,
        created_by="admin_1",
        published_by="admin_1",
        published_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        product_profile_id=11,
    )

    class BotTeams:
        def get_bot_team(self, **kwargs: object) -> BotTeamDetail:
            return BotTeamDetail(bot_team=bot_team, revisions=(revision,))

    class Templates:
        def create_todo_from_template(self, **kwargs: object) -> TodoRecord:
            return _todo()

    class Todos:
        pass

    return TodoProductCreationService(
        todos=Todos(),
        bot_teams=BotTeams(),
        template_creation=Templates(),
        product_profiles=profiles,
        repository=repository,
    )


@pytest.mark.asyncio
async def test_authenticated_actor_context_and_protocol_result_are_used_by_ensure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentclaw.community.core.task.todos.bot_team_models import (
        BotTeamRecord,
        BotTeamRevisionRecord,
        BotTeamDetail,
        BotTeamStatus,
    )
    from agentclaw.community.core.task.todos.services.todo_product_creation_service import (
        TodoProductCreationService,
    )
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
    from agentclaw.community.utils.env_utils import get_current_env

    profiles = RecordingProductProfiles()
    bot_team = BotTeamRecord(
        bot_team_id="todo_tpl_1",
        space_id=7,
        name="Coding delivery",
        description="Analyze, implement, verify",
        status=BotTeamStatus.ACTIVE,
        published_revision_id="todo_tplv_1",
        created_by="admin_1",
        updated_by="admin_1",
        created_at=NOW,
        updated_at=NOW,
    )
    revision = BotTeamRevisionRecord(
        bot_team_revision_id="todo_tplv_1",
        bot_team_id="todo_tpl_1",
        revision_no=1,
        name=bot_team.name,
        description=bot_team.description,
        nodes=(),
        definition_hash="1" * 64,
        created_by="admin_1",
        published_by="admin_1",
        published_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        product_profile_id=11,
    )

    class BotTeams:
        def get_bot_team(self, **kwargs: object) -> BotTeamDetail:
            return BotTeamDetail(bot_team=bot_team, revisions=(revision,))

    class Templates:
        def create_todo_from_template(self, **kwargs: object) -> TodoRecord:
            return _todo()

    class Repository:
        def __init__(self, *, binding_conflict: bool = False) -> None:
            self.calls: list[object] = []
            self.binding_conflict = binding_conflict

        def get_todo(self, **kwargs: object) -> TodoRecord:
            return _todo(product_collet_id=None)

        def list_stages(self, **kwargs: object) -> tuple[TodoStageRecord, ...]:
            return (_stage(0),)

        def get_source_binding(self, **kwargs: object) -> TodoSourceInput | None:
            return TodoSourceInput(
                source_type="dima",
                container_id="P1",
                item_id="DIM-1",
            )

        def bind_product_collet(self, **kwargs: object) -> TodoRecord:
            self.calls.append(kwargs)
            if self.binding_conflict:
                raise TodoStateConflictError("different Product Collet")
            return _todo(product_collet_id=kwargs["product_collet_id"])

    repository = Repository()

    class Todos:
        pass

    service = TodoProductCreationService(
        todos=Todos(),
        bot_teams=BotTeams(),
        template_creation=Templates(),
        product_profiles=profiles,
        repository=repository,
    )

    with avernet_tenant_scope("tenant-a"):
        result = await service.ensure_product_collet(
            todo_id="todo_1", actor_id="creator_1"
        )

    assert result.status is TodoProductColletStatus.READY
    assert result.todo.product_collet_id == 22
    assert len(profiles.calls) == 1
    call = profiles.calls[0]
    assert call["profile_id"] == 11
    assert call["request_id"] == f"todo:collet:tenant-a:{get_current_env()}:todo_1:v1"
    context = call["context"]
    assert isinstance(context, ActorContext)
    assert context.actor == PrincipalRef("human", "creator_1")
    assert context.source == "todo_product"


@pytest.mark.asyncio
async def test_product_collet_failure_is_retryable_and_reuses_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = RecordingProductProfiles(failure=RuntimeError("protocol unavailable"))
    repository = _pending_runtime_repository()
    service = _service_for_pending_todo(profiles, repository)

    with avernet_tenant_scope("tenant-a"):
        first = await service.ensure_product_collet(
            todo_id="todo_1", actor_id="creator_1"
        )
        second = await service.ensure_product_collet(
            todo_id="todo_1", actor_id="creator_1"
        )

    assert first.status is TodoProductColletStatus.RETRYABLE_FAILURE
    assert first.error_code == "product_runtime_unavailable"
    assert first.todo.product_collet_id is None
    assert second.status is TodoProductColletStatus.RETRYABLE_FAILURE
    assert len(profiles.calls) == 2
    assert profiles.calls[0]["request_id"] == profiles.calls[1]["request_id"]
    assert not repository.calls


@pytest.mark.asyncio
async def test_cas_conflict_does_not_overwrite_another_product_collet() -> None:
    profiles = RecordingProductProfiles()
    repository = _pending_runtime_repository(binding_conflict=True)
    service = _service_for_pending_todo(profiles, repository)

    result = await service.ensure_product_collet(todo_id="todo_1", actor_id="creator_1")

    assert result.status is TodoProductColletStatus.BINDING_CONFLICT
    assert result.error_code == "product_collet_conflict"
    assert result.todo.product_collet_id is None
    assert len(repository.calls) == 1
