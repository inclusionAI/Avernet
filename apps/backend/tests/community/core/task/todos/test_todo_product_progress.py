from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.stage_rollout import (
    StageRollout,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    RolloutList,
    StageRolloutDetails,
)
from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDetail,
    BotTeamRecord,
    BotTeamRevisionRecord,
    BotTeamStatus,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.progress_models import (
    TodoProgressStageStatus,
)
from agentclaw.community.core.task.todos.services.todo_product_progress_service import (
    TodoProductProgressService,
)
from agentclaw.community.core.task.todos.services.todo_product_progress_service import (
    parse_product_rollouts,
    map_product_stage_status,
)
from agentclaw.community.core.task.todos.notification_models import (
    TodoNotificationEvent,
    TodoNotificationPublishResult,
    TodoNotificationPublishStatus,
    TodoNotificationType,
    build_todo_notification_delivery_key,
)
from agentclaw.community.core.task.todos.stage_models import (
    TodoStageRecord,
    TodoStageStatus,
)

NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


def _todo(**overrides: object) -> TodoRecord:
    values: dict[str, object] = {
        "todo_id": "todo_1",
        "space_id": 7,
        "title": "Implement feature",
        "description": "Implement Progress",
        "bot_id": "bot_team_1",
        "bot_name": "Coding team",
        "status": TodoStatus.IN_PROGRESS,
        "collab_context_id": None,
        "created_by": "creator_1",
        "created_at": NOW,
        "updated_at": NOW,
        "template_id": "todo_tpl_1",
        "template_version_id": "todo_tplv_1",
        "template_name_snapshot": "Coding delivery",
        "product_collet_id": 22,
    }
    values.update(overrides)
    return TodoRecord(**values)  # type: ignore[arg-type]


def _stage(
    order: int,
    *,
    status: TodoStageStatus = TodoStageStatus.IN_PROGRESS,
) -> TodoStageRecord:
    return TodoStageRecord(
        stage_instance_id=f"stage_{order}",
        todo_id="todo_1",
        template_version_id="todo_tplv_1",
        stage_key=f"stage_{order}",
        stage_order=order,
        stage_name=f"Stage {order}",
        purpose=f"Purpose {order}",
        stage_kind="bot" if order == 0 else "human",
        executor_binding_key="executor",
        executor_subject_type="bot" if order == 0 else "human",
        executor_subject_id="bot_1" if order == 0 else "owner_1",
        executor_subject_name="Coding Bot" if order == 0 else "Owner One",
        owner_binding_key="owner",
        owner_user_id="owner_1",
        owner_user_name="Owner One",
        completion_gate_json='{"required_artifacts":[]}',
        status=status,
        collab_context_id=None,
        pending_action_kind=None,
        state_reason=None,
        requested_action=None,
        started_at=NOW,
        completed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _runtime_stage(
    order: int,
    *,
    status: StatusEnum = StatusEnum.RUNNING,
    stage_rollout_id: int | None = None,
) -> StageRollout:
    return StageRollout(
        name=f"stage_{order}",
        stage_rollout_id=stage_rollout_id or 1000 + order,
        status=status,
        assignee_type="bot" if order == 0 else "human",
        assignee_uuid="bot_1" if order == 0 else "owner_1",
        input="Execute stage",
        session_id=f"session_{order}",
        artifacts=[],
        acceptances=[],
        error_msg="",
    )


def _rollout(*, rollout_id: int = 501) -> ColletRollout:
    return ColletRollout(
        collet_id=22,
        rollout_id=rollout_id,
        stages=[
            _runtime_stage(0, status=StatusEnum.ACCEPTANCE_SUCCEED),
            _runtime_stage(1, status=StatusEnum.RUNNING),
        ],
        content="todo snapshot",
        status=StatusEnum.RUNNING,
    )


@dataclass
class _Detail:
    status: StatusEnum = StatusEnum.RUNNING


class RecordingEvents:
    calls: list[dict[str, Any]] = []

    def list_stages(self, **kwargs: Any) -> tuple[TodoStageRecord, ...]:
        self.calls.append({"op": "list_stages", **kwargs})
        return (_stage(1), _stage(2, status=TodoStageStatus.NOT_STARTED))


class _Todos:
    def __init__(self, todo: TodoRecord) -> None:
        self.todo = todo
        self.calls: list[dict[str, Any]] = []

    def get_todo(self, **kwargs: Any) -> TodoRecord:
        self.calls.append({"op": "get_todo", **kwargs})
        return self.todo

    def list_stages(self, **kwargs: Any) -> tuple[TodoStageRecord, ...]:
        self.calls.append({"op": "list_stages", **kwargs})
        return (_stage(1), _stage(2, status=TodoStageStatus.NOT_STARTED))


class _BotTeams:
    def __init__(self, profile_id: int | None = 55) -> None:
        self.profile_id = profile_id

    def get_bot_team(self, **kwargs: Any) -> BotTeamDetail:
        team = BotTeamRecord(
            bot_team_id="todo_tpl_1",
            space_id=7,
            name="Coding delivery",
            description="",
            status=BotTeamStatus.ACTIVE,
            published_revision_id="todo_tplv_1",
            created_by="creator_1",
            updated_by="creator_1",
            created_at=NOW,
            updated_at=NOW,
        )
        revision = BotTeamRevisionRecord(
            bot_team_revision_id="todo_tplv_1",
            bot_team_id=team.bot_team_id,
            revision_no=1,
            name=team.name,
            description="",
            nodes=(),
            definition_hash="a" * 64,
            created_by="creator_1",
            published_by="creator_1",
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            product_profile_id=self.profile_id,
        )
        return BotTeamDetail(bot_team=team, revisions=(revision,))


class RecordingNotifications:
    def __init__(self) -> None:
        self.events: list[TodoNotificationEvent] = []

    def publish(self, event: TodoNotificationEvent) -> TodoNotificationPublishResult:
        self.events.append(event)
        return TodoNotificationPublishResult(
            TodoNotificationPublishStatus.PUBLISHED, None
        )


class RecordingProductProfiles:
    def __init__(
        self,
        *,
        rollouts: list[ColletRollout] | None = None,
        metadata: object = "present",
        details: StageRolloutDetails | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.rollouts = rollouts or [_rollout(rollout_id=500), _rollout(rollout_id=501)]
        self.metadata = metadata
        self.details = details or StageRolloutDetails(
            stage_rollout_id=1001,
            session_id="session_1",
            messages=[
                {"message_id": "m1", "text": "开始执行"},
                "not-a-message",
                {"message_id": "m2", "text": "执行失败", "error_code": "timeout"},
            ],
            bound=True,
            limit=50,
            complete=False,
        )
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    async def query_collet_rollout(
        self, collet_id: int, all_rollout: bool = True, *, context: ActorContext
    ) -> list[ColletRollout]:
        self.calls.append(
            {
                "op": "query_collet_rollout",
                "collet_id": collet_id,
                "all_rollout": all_rollout,
                "context": context,
            }
        )
        if self.failure is not None:
            raise self.failure
        values = list(self.rollouts)
        if self.metadata == "present":
            return RolloutList(values, 4, values[-1].rollout_id, False)
        return values

    async def query_stage_rollout_details(
        self, stage_rollout_id: int, *, context: ActorContext, limit: int = 50
    ) -> StageRolloutDetails:
        self.calls.append(
            {
                "op": "query_stage_rollout_details",
                "stage_rollout_id": stage_rollout_id,
                "context": context,
                "limit": limit,
            }
        )
        if self.failure is not None:
            raise self.failure
        return self.details


def _service(
    *,
    todo: TodoRecord = _todo(),
    profile_id: int | None = 55,
    products: RecordingProductProfiles | None = None,
    notifications: RecordingNotifications | None = None,
) -> tuple[
    TodoProductProgressService,
    _Todos,
    _BotTeams,
    RecordingProductProfiles,
    RecordingNotifications,
]:
    todos = _Todos(todo)
    teams = _BotTeams(profile_id)
    profiles = products or RecordingProductProfiles()
    publisher = notifications or RecordingNotifications()
    return (
        TodoProductProgressService(
            todos=todos,
            bot_teams=teams,
            product_profiles=profiles,
            notifications=publisher,
        ),
        todos,
        teams,
        profiles,
        publisher,
    )


def test_product_stage_status_mapping_is_exhaustive_and_fail_closed() -> None:
    expected = {
        StatusEnum.CREATE: TodoProgressStageStatus.NOT_STARTED,
        StatusEnum.RUNNING: TodoProgressStageStatus.IN_PROGRESS,
        StatusEnum.RUN_FINISHED: TodoProgressStageStatus.PENDING_ACTION,
        StatusEnum.RUN_FAILED: TodoProgressStageStatus.INTERRUPTED,
        StatusEnum.ACCEPTANCE_SUCCEED: TodoProgressStageStatus.COMPLETED,
        StatusEnum.ACCEPTANCE_FAILED: TodoProgressStageStatus.PENDING_ACTION,
    }
    assert {status: map_product_stage_status(status) for status in StatusEnum} == expected
    with pytest.raises(ValueError, match="unknown Product runtime stage status"):
        map_product_stage_status("UNEXPECTED")  # type: ignore[arg-type]


def test_rollout_parser_uses_metadata_and_current_id_not_order() -> None:
    older, current = _rollout(rollout_id=500), _rollout(rollout_id=501)
    value = RolloutList([older, current], 7, current.rollout_id, False)

    parsed = parse_product_rollouts(value)

    assert parsed.revision == 7
    assert parsed.current_rollout_id == 501
    assert parsed.stopped is False
    assert parsed.current.rollout_id == 501


def test_rollout_parser_rejects_missing_metadata() -> None:
    with pytest.raises(ValueError, match="Product rollout metadata is unavailable"):
        parse_product_rollouts([_rollout()])


def test_service_queries_current_rollout_and_only_the_current_stage_details() -> None:
    service, _todos, _teams, products, _notifications = _service()

    progress = asyncio_run(
        service.get_progress(todo_id="todo_1", actor_id="human_1", limit=25)
    )

    assert progress.runtime_kind.value == "product_collet"
    assert progress.runtime_unavailable is False
    assert progress.rollout_revision == 4
    assert progress.stopped is False
    assert progress.current_stage_code == "stage_1"
    assert [stage.code for stage in progress.stages] == ["stage_1", "stage_2"]
    assert progress.stages[0].status is TodoProgressStageStatus.IN_PROGRESS
    assert progress.stages[0].attempt_count == 2
    assert products.calls[0]["collet_id"] == 22
    assert products.calls[0]["all_rollout"] is True
    assert products.calls[-1]["stage_rollout_id"] == 1001
    assert products.calls[-1]["limit"] == 25
    assert progress.stages[0].steps != ()


def test_service_keeps_skeleton_when_runtime_is_unavailable() -> None:
    products = RecordingProductProfiles(failure=TimeoutError("timeout"))
    service, _todos, _teams, product_profiles, _notifications = _service(products=products)

    progress = asyncio_run(
        service.get_progress(todo_id="todo_1", actor_id="human_1")
    )

    assert progress.runtime_unavailable is True
    assert progress.runtime_error_code == "product_progress_unavailable"
    assert [stage.code for stage in progress.stages] == ["stage_1", "stage_2"]
    assert all(stage.status is TodoProgressStageStatus.UNKNOWN for stage in progress.stages)


def test_legacy_todo_never_calls_product_runtime() -> None:
    products = RecordingProductProfiles()
    service, _todos, _teams, product_profiles, _notifications = _service(
        todo=_todo(
            template_id="",
            template_version_id="",
            product_collet_id=None,
            collab_context_id="context_1",
        ),
        products=products,
    )

    progress = asyncio_run(service.get_progress(todo_id="todo_1", actor_id="human_1"))

    assert progress.runtime_kind.value == "legacy_context"
    assert product_profiles.calls == []
    assert progress.runtime_unavailable is False
    assert progress.stages[0].status is TodoProgressStageStatus.IN_PROGRESS


def test_product_pending_reports_provisioning_without_runtime_query() -> None:
    products = RecordingProductProfiles()
    service, _todos, _teams, product_profiles, _notifications = _service(
        todo=_todo(product_collet_id=None), products=products
    )

    progress = asyncio_run(service.get_progress(todo_id="todo_1", actor_id="human_1"))

    assert progress.runtime_kind.value == "product_runtime_pending"
    assert product_profiles.calls == []
    assert progress.current_stage_code == "stage_1"
    assert all(
        stage.status is TodoProgressStageStatus.NOT_STARTED for stage in progress.stages
    )


def asyncio_run(coroutine: Any):
    import asyncio

    return asyncio.run(coroutine)


def _single_stage_rollout(status: StatusEnum) -> ColletRollout:
    return ColletRollout(
        collet_id=22,
        rollout_id=501,
        stages=[
            replace(
                _runtime_stage(1, status=status, stage_rollout_id=1001),
                status=status,
            )
        ],
        content="todo snapshot",
        status=StatusEnum.RUNNING,
    )


@pytest.mark.parametrize(
    ("runtime_status", "notification_type"),
    [
        (StatusEnum.RUN_FINISHED, TodoNotificationType.HUMAN_ACTION_REQUIRED),
        (StatusEnum.ACCEPTANCE_FAILED, TodoNotificationType.VERIFICATION_REQUIRED),
        (StatusEnum.RUN_FAILED, TodoNotificationType.EXECUTION_INTERRUPTED),
        (StatusEnum.ACCEPTANCE_SUCCEED, TodoNotificationType.TODO_COMPLETED),
    ],
)
def test_progress_goals_notify_owner_with_stable_delivery_identity(
    runtime_status: StatusEnum, notification_type: TodoNotificationType
) -> None:
    products = RecordingProductProfiles(
        rollouts=[_single_stage_rollout(runtime_status)]
    )
    service, _todos, _teams, _product_profiles, notifications = _service(
        todo=_todo(owner_id="owner_1"), products=products
    )

    progress = asyncio_run(service.get_progress(todo_id="todo_1", actor_id="human_1"))
    asyncio_run(service.get_progress(todo_id="todo_1", actor_id="human_1"))

    assert len(notifications.events) == 2
    assert all(
        item.notification_type is notification_type for item in notifications.events
    )
    assert notifications.events[0].recipient_id == "owner_1"
    assert notifications.events[0].notification_type is notification_type
    assert notifications.events[0].delivery_key == (
        build_todo_notification_delivery_key(
            todo_id="todo_1",
            notification_type=notification_type,
            rollout_revision=4,
            stage_rollout_id=1001,
            action_version=notifications.events[0].action_version,
        )
    )
    assert len({item.delivery_key for item in notifications.events}) == 1
    assert progress.runtime_unavailable is False
