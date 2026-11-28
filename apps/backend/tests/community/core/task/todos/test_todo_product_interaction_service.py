from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabVersionConflict,
)
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
    InteractiveResult,
    RolloutList,
)
from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDetail,
    BotTeamRecord,
    BotTeamRevisionRecord,
    BotTeamStatus,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.product_interaction_models import (
    TodoProductInteractionResponse,
    TodoProductInteraction,
    TodoProductInteractionDeliveryState,
    TodoProductInteractionKind,
)
from agentclaw.community.core.task.todos.services.todo_product_interaction_service import (
    TodoProductInteractionService,
)

NOW = datetime(2026, 11, 28, tzinfo=timezone.utc)


@dataclass
class _Repository:
    rows: dict[str, TodoProductInteraction]

    def _row(self, interaction_id: str) -> TodoProductInteraction:
        if interaction_id not in self.rows:
            raise KeyError(interaction_id)
        return self.rows[interaction_id]

    def _update(
        self, interaction_id: str, **changes: Any
    ) -> TodoProductInteraction:
        row = self._row(interaction_id)
        result = TodoProductInteraction(**{**row.__dict__, **changes})
        self.rows[interaction_id] = result
        return result

    def create_interaction_single_flight(
        self,
        *,
        todo_id: str,
        interaction_id: str,
        kind: TodoProductInteractionKind,
        actor_id: str,
        actor_name: str | None,
        message: str,
        product_collet_id: int,
        reply_to_interaction_id: str | None = None,
    ) -> TodoProductInteraction:
        row = TodoProductInteraction(
            interaction_id=interaction_id,
            todo_id=todo_id,
            kind=kind,
            actor_id=actor_id,
            actor_name=actor_name,
            request_message=message,
            delivery_state=TodoProductInteractionDeliveryState.SENDING,
            product_collet_id=product_collet_id,
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
            target_interaction_id=reply_to_interaction_id,
        )
        self.rows[interaction_id] = row
        return row

    def request_cancel_and_create_stop(
        self,
        *,
        todo_id: str,
        interaction_id: str,
        stop_interaction_id: str,
        actor_id: str,
        actor_name: str | None,
        message: str,
        product_collet_id: int,
    ) -> TodoProductInteraction:
        self._update(
            interaction_id,
            delivery_state=TodoProductInteractionDeliveryState.CANCEL_REQUESTED,
        )
        stop = TodoProductInteraction(
            interaction_id=stop_interaction_id,
            todo_id=todo_id,
            kind=TodoProductInteractionKind.STOP_REQUEST,
            actor_id=actor_id,
            actor_name=actor_name,
            request_message=message,
            delivery_state=TodoProductInteractionDeliveryState.SENDING,
            product_collet_id=product_collet_id,
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
            target_interaction_id=interaction_id,
        )
        self.rows[stop_interaction_id] = stop
        return stop

    def get_interaction(self, **kwargs: Any) -> TodoProductInteraction:
        return self._row(kwargs["interaction_id"])

    def list_interactions(
        self, *, todo_id: str, page: int, page_size: int
    ) -> tuple[TodoProductInteraction, ...]:
        return tuple(row for row in self.rows.values() if row.todo_id == todo_id)

    def get_blocking_interaction(self, *, todo_id: str):
        return next(
            (
                row
                for row in self.rows.values()
                if row.todo_id == todo_id
                and row.kind is TodoProductInteractionKind.HUMAN_MESSAGE
                and row.delivery_state.value
                in {"sending", "delivery_unknown", "cancel_requested"}
            ),
            None,
        )

    def save_rollout_fence(
        self, *, interaction_id: str, collet_rollout_id: int, expected_revision: int
    ) -> TodoProductInteraction:
        return self._update(
            interaction_id,
            collet_rollout_id=collet_rollout_id,
            expected_revision=expected_revision,
        )

    def mark_delivery_unknown(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction:
        return self._update(
            interaction_id,
            delivery_state=TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN,
            failure_code=failure_code,
        )

    def complete_interaction(
        self, *, interaction_id: str, response_type: str, response_message: str
    ) -> TodoProductInteraction:
        return self._update(
            interaction_id,
            delivery_state=TodoProductInteractionDeliveryState.SUCCEEDED,
            response=TodoProductInteractionResponse(
                type=response_type, message=response_message
            ),
            failure_code=None,
        )

    def fail_interaction(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction:
        return self._update(
            interaction_id,
            delivery_state=TodoProductInteractionDeliveryState.FAILED,
            failure_code=failure_code,
        )

    def mark_cancelled(self, *, todo_id: str, interaction_id: str) -> TodoProductInteraction:
        return self._update(
            interaction_id,
            delivery_state=TodoProductInteractionDeliveryState.CANCELLED,
        )


class _RepositorySaveFailure(_Repository):
    def save_rollout_fence(
        self, *, interaction_id: str, collet_rollout_id: int, expected_revision: int
    ) -> TodoProductInteraction:
        raise RuntimeError("database write failed")


class _RepositoryFailWriteFailure(_Repository):
    def fail_interaction(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction:
        raise RuntimeError("database write failed")


class _RepositoryUnknownWriteFailure(_Repository):
    def mark_delivery_unknown(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction:
        raise RuntimeError("database write failed")


class _Todos:
    def __init__(self) -> None:
        self.todo = _todo()

    def get_todo(self, **kwargs: Any) -> TodoRecord:
        return self.todo


class _BotTeams:
    def get_bot_team(self, **kwargs: Any) -> BotTeamDetail:
        team = BotTeamRecord(
            bot_team_id="todo_tpl_1",
            space_id=7,
            name="Coding team",
            description="",
            status=BotTeamStatus.ACTIVE,
            published_revision_id="todo_tplv_1",
            created_by="human_1",
            updated_by="human_1",
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
            created_by="human_1",
            published_by="human_1",
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            product_profile_id=55,
        )
        return BotTeamDetail(bot_team=team, revisions=(revision,))


class _ProductProfiles:
    def __init__(
        self,
        *,
        result: InteractiveResult | None = None,
        query_failure: Exception | None = None,
        interactive_failure: Exception | None = None,
    ) -> None:
        self.result = result or InteractiveResult(type="RE_DO", message="已重新执行")
        self.query_failure = query_failure
        self.interactive_failure = interactive_failure
        self.calls: list[dict[str, Any]] = []

    async def query_collet_rollout(
        self, collet_id: int, all_rollout: bool = True, *, context: ActorContext
    ):
        self.calls.append({"op": "query", "collet_id": collet_id})
        if self.query_failure is not None:
            raise self.query_failure
        values = [_rollout()]
        return RolloutList(values, 7, values[-1].rollout_id, False)

    async def interactive(
        self,
        rollout_id: int,
        message: str,
        *,
        request_id: str,
        expected_revision: int,
        context: ActorContext,
    ):
        self.calls.append(
            {
                "op": "interactive",
                "rollout_id": rollout_id,
                "message": message,
                "request_id": request_id,
                "expected_revision": expected_revision,
                "context": context,
            }
        )
        if self.interactive_failure is not None:
            raise self.interactive_failure
        return self.result


class _Stages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def cancel(self, **kwargs: Any) -> TodoRecord:
        self.calls.append(kwargs)
        return _todo(status=TodoStatus.CANCELLED)


class _Recorder:
    def __init__(self, product: _ProductProfiles) -> None:
        self.repository = _Repository({})
        self.product = product


def _todo(status: TodoStatus = TodoStatus.IN_PROGRESS) -> TodoRecord:
    return TodoRecord(
        todo_id="todo_1",
        space_id=7,
        title="Implement feature",
        description="",
        bot_id="team_1",
        bot_name="Coding team",
        status=status,
        collab_context_id=None,
        created_by="human_1",
        created_at=NOW,
        updated_at=NOW,
        template_id="todo_tpl_1",
        template_version_id="todo_tplv_1",
        template_name_snapshot="Coding team",
        product_collet_id=22,
        owner_id="owner_1",
        owner_name="Owner One",
    )


def _rollout() -> ColletRollout:
    stage = StageRollout(
        name="stage_1",
        stage_rollout_id=1001,
        status=StatusEnum.RUNNING,
        assignee_type="bot",
        assignee_uuid="bot_1",
        input="input",
        session_id="session_1",
        artifacts=[],
        acceptances=[],
        error_msg="",
    )
    return ColletRollout(
        collet_id=22,
        rollout_id=501,
        stages=[stage],
        content="todo snapshot",
        status=StatusEnum.RUNNING,
    )


def _service(
    *,
    product: _ProductProfiles | None = None,
    repository: _Repository | None = None,
    stages: _Stages | None = None,
) -> tuple[
    TodoProductInteractionService,
    _Repository,
    _ProductProfiles,
    _Stages,
]:
    recorder = _Recorder(product or _ProductProfiles())
    repository = repository or recorder.repository
    stages = stages or _Stages()
    return (
        TodoProductInteractionService(
            todos=_Todos(),
            bot_teams=_BotTeams(),
            product_profiles=recorder.product,
            repository=repository,
            stages=stages,
        ),
        repository,
        recorder.product,
        stages,
    )


def test_send_uses_local_request_id_and_current_rollout_metadata() -> None:
    service, repository, products, _stages = _service()

    result = asyncio.run(
        service.send_interaction(
            todo_id="todo_1", actor_id="human_1", message="请重新执行"
        )
    )

    assert result.delivery_state is TodoProductInteractionDeliveryState.SUCCEEDED
    assert result.response is not None
    assert result.response.type == "RE_DO"
    assert products.calls[-1]["rollout_id"] == 501
    assert products.calls[-1]["expected_revision"] == 7
    assert repository.rows[result.interaction_id].request_message == "请重新执行"


def test_version_conflict_is_failed_not_replayed() -> None:
    service, repository, _products, _stages = _service(
        product=_ProductProfiles(
            interactive_failure=ContextCollabVersionConflict("stale")
        )
    )

    result = asyncio.run(
        service.send_interaction(
            todo_id="todo_1", actor_id="human_1", message="请重新执行"
        )
    )

    assert result.delivery_state is TodoProductInteractionDeliveryState.FAILED
    assert result.failure_code == "stale_rollout_revision"
    assert len(_products.calls) == 2


def test_timeout_becomes_delivery_unknown() -> None:
    service, _repository, _products, _stages = _service(
        product=_ProductProfiles(
            interactive_failure=TimeoutError("timeout")
        )
    )

    result = asyncio.run(
        service.send_interaction(
            todo_id="todo_1", actor_id="human_1", message="请重新执行"
        )
    )

    assert result.delivery_state is TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN
    assert result.failure_code == "interaction_delivery_unknown"


def test_cancel_only_project_cancelled_after_protocol_shutdown() -> None:
    products = _ProductProfiles(
        result=InteractiveResult(type="SHUTDOWN", message="已停止")
    )
    service, repository, _products, stages = _service(product=products)
    repository.create_interaction_single_flight(
        todo_id="todo_1",
        interaction_id="int_1",
        kind=TodoProductInteractionKind.HUMAN_MESSAGE,
        actor_id="human_1",
        actor_name=None,
        message="请继续",
        product_collet_id=22,
    )

    stop = asyncio.run(
        service.cancel_interaction(
            todo_id="todo_1", actor_id="human_1", interaction_id="int_1"
        )
    )

    assert stop.interaction_id == "int_1:shutdown:v1"
    assert stop.delivery_state is TodoProductInteractionDeliveryState.SUCCEEDED
    assert repository.rows["int_1"].delivery_state is (
        TodoProductInteractionDeliveryState.CANCELLED
    )
    assert stages.calls[0]["todo_id"] == "todo_1"


def test_local_rollout_fence_write_failure_is_not_hidden_as_runtime_failure() -> None:
    repository = _RepositorySaveFailure({})
    service, _repository, _products, _stages = _service(repository=repository)

    with pytest.raises(RuntimeError, match="database write failed"):
        asyncio.run(
            service.send_interaction(
                todo_id="todo_1", actor_id="human_1", message="请重新执行"
            )
        )


def test_failed_state_write_failure_is_reported() -> None:
    service, repository, _products, _stages = _service(
        product=_ProductProfiles(query_failure=RuntimeError("runtime unavailable")),
        repository=_RepositoryFailWriteFailure({}),
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        asyncio.run(
            service.send_interaction(
                todo_id="todo_1", actor_id="human_1", message="请重新执行"
            )
        )
    assert repository is not None


def test_unknown_state_write_failure_is_reported() -> None:
    service, repository, _products, _stages = _service(
        product=_ProductProfiles(interactive_failure=TimeoutError("timeout")),
        repository=_RepositoryUnknownWriteFailure({}),
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        asyncio.run(
            service.send_interaction(
                todo_id="todo_1", actor_id="human_1", message="请重新执行"
            )
        )
    assert repository is not None
