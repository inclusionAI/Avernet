"""Project Todo stages and Product runtime facts into a stable Progress VO."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
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
    ProductProfileServiceProtocol,
    StageRolloutDetails,
)
from agentclaw.community.core.task.todos.bot_team_service_protocol import (
    TodoBotTeamServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import TodoNotFoundError
from agentclaw.community.core.task.todos.models import (
    MAX_ACTOR_ID_LENGTH,
    MAX_TODO_ID_LENGTH,
    TodoRecord,
)
from agentclaw.community.core.task.todos.product_creation_models import (
    TodoRuntimeKind,
    resolve_todo_runtime_kind,
)
from agentclaw.community.core.task.todos.progress_models import (
    TodoProgress,
    TodoProgressBlocker,
    TodoProgressOutput,
    TodoProgressStageStatus,
    TodoProgressStep,
    TodoStageProgress,
)
from agentclaw.community.core.task.todos.product_progress_protocol import (
    TodoProductProgressServiceProtocol,
)
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.core.task.todos.stage_models import (
    TodoStageRecord,
    TodoStageStatus,
)

_DEFAULT_MESSAGE_LIMIT = 50
_MAX_MESSAGE_LIMIT = 200
_MAX_SAFE_TEXT_LENGTH = 500


@dataclass(frozen=True)
class ProductRolloutSnapshot:
    """Validated runtime result retained in one request only."""

    revision: int
    current_rollout_id: int
    stopped: bool
    rollouts: tuple[ColletRollout, ...]
    current: ColletRollout


def parse_product_rollouts(value: Any) -> ProductRolloutSnapshot:
    """Read the transport metadata carrier without trusting list position."""
    revision = getattr(value, "revision", None)
    current_rollout_id = getattr(value, "current_rollout_id", None)
    stopped = getattr(value, "stopped", None)
    if type(revision) is not int or type(current_rollout_id) is not int:
        raise ValueError("Product rollout metadata is unavailable")
    if type(stopped) is not bool or not isinstance(value, list):
        raise ValueError("Product rollout metadata is unavailable")
    rollouts = tuple(value)
    current = next(
        (rollout for rollout in rollouts if rollout.rollout_id == current_rollout_id),
        None,
    )
    if current is None:
        raise ValueError("Product current rollout is outside the returned list")
    return ProductRolloutSnapshot(
        revision=revision,
        current_rollout_id=current_rollout_id,
        stopped=stopped,
        rollouts=rollouts,
        current=current,
    )


def map_product_stage_status(status: Any) -> TodoProgressStageStatus:
    """Exhaustively map the closed runtime enum; unknown values fail closed."""
    if status is StatusEnum.CREATE:
        return TodoProgressStageStatus.NOT_STARTED
    if status is StatusEnum.RUNNING:
        return TodoProgressStageStatus.IN_PROGRESS
    if status is StatusEnum.RUN_FINISHED:
        return TodoProgressStageStatus.PENDING_ACTION
    if status is StatusEnum.RUN_FAILED:
        return TodoProgressStageStatus.INTERRUPTED
    if status is StatusEnum.ACCEPTANCE_SUCCEED:
        return TodoProgressStageStatus.COMPLETED
    if status is StatusEnum.ACCEPTANCE_FAILED:
        return TodoProgressStageStatus.PENDING_ACTION
    raise ValueError(f"unknown Product runtime stage status: {status!r}")


def _local_stage_status(status: TodoStageStatus) -> TodoProgressStageStatus:
    if status is TodoStageStatus.NOT_STARTED:
        return TodoProgressStageStatus.NOT_STARTED
    if status is TodoStageStatus.IN_PROGRESS:
        return TodoProgressStageStatus.IN_PROGRESS
    if status is TodoStageStatus.BLOCKED:
        return TodoProgressStageStatus.PENDING_ACTION
    if status is TodoStageStatus.COMPLETED:
        return TodoProgressStageStatus.COMPLETED
    return TodoProgressStageStatus.UNKNOWN


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:_MAX_SAFE_TEXT_LENGTH]


def _message_id(value: Any, index: int) -> str:
    raw = (
        value.get("message_id")
        if isinstance(value, dict)
        else value.get("id")
        if isinstance(value, dict)
        else None
    )
    text = _safe_text(raw)
    return text or f"message-{index + 1}"


def _message_text(value: dict[str, Any]) -> str:
    for key in ("text", "content", "message"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return _safe_text(candidate)
        if isinstance(candidate, dict):
            nested = candidate.get("text")
            if isinstance(nested, str):
                return _safe_text(nested)
    return ""


def _messages_steps(
    messages: list[Any],
) -> tuple[tuple[TodoProgressStep, ...], int]:
    steps: list[TodoProgressStep] = []
    ignored = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            ignored += 1
            continue
        text = _message_text(message)
        if not text:
            ignored += 1
            continue
        error = _safe_text(message.get("error_code"))
        occurred = _safe_text(message.get("created_at") or message.get("timestamp"))
        steps.append(
            TodoProgressStep(
                step_id=_message_id(message, index),
                name=text,
                status=(
                    TodoProgressStageStatus.INTERRUPTED
                    if error
                    else TodoProgressStageStatus.IN_PROGRESS
                ),
                detail=text,
                occurred_at=occurred or None,
            )
        )
    return tuple(steps), ignored


def _artifact_output(artifact: Any) -> TodoProgressOutput | None:
    name = _safe_text(getattr(artifact, "name", ""))
    kind = _safe_text(getattr(artifact, "type", "artifact"))
    if not name and not kind:
        return None
    content = getattr(artifact, "content", None)
    summary = ""
    if hasattr(content, "text"):
        summary = _safe_text(content.text)
    elif hasattr(content, "link"):
        summary = _safe_text(content.link)
    elif hasattr(content, "file_name"):
        summary = _safe_text(content.file_name)
    return TodoProgressOutput(
        artifact_id=None,
        kind=kind or "artifact",
        name=name or kind,
        summary=summary,
    )


def _stage_outputs(stage: StageRollout) -> tuple[TodoProgressOutput, ...]:
    outputs: list[TodoProgressOutput] = []
    for artifact in stage.artifacts:
        output = _artifact_output(artifact)
        if output is not None:
            outputs.append(output)
    return tuple(outputs)


class TodoProductProgressService(TodoProductProgressServiceProtocol):
    """Build the stage-first progress view while protecting the Product boundary."""

    @inject
    def __init__(
        self,
        todos: TodoServiceProtocol,
        bot_teams: TodoBotTeamServiceProtocol,
        product_profiles: ProductProfileServiceProtocol,
    ) -> None:
        self._todos = todos
        self._bot_teams = bot_teams
        self._product_profiles = product_profiles

    async def get_progress(
        self,
        *,
        todo_id: str,
        actor_id: str,
        expanded_stage_code: str | None = None,
        limit: int = _DEFAULT_MESSAGE_LIMIT,
    ) -> TodoProgress:
        normalized_todo_id = self._required_text(
            todo_id, "todo_id", MAX_TODO_ID_LENGTH
        )
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        normalized_expanded_stage = self._optional_text(
            expanded_stage_code, "expanded_stage_code", 256
        )
        if type(limit) is not int or not 1 <= limit <= _MAX_MESSAGE_LIMIT:
            raise ValidationError("message limit must be an integer in 1..200")

        todo = await asyncio.to_thread(
            self._todos.get_todo,
            todo_id=normalized_todo_id,
            actor_id=normalized_actor_id,
        )
        stages = await asyncio.to_thread(
            self._todos.list_stages,
            todo_id=normalized_todo_id,
            actor_id=normalized_actor_id,
        )
        if normalized_expanded_stage is not None and not any(
            stage.stage_key == normalized_expanded_stage for stage in stages
        ):
            raise TodoNotFoundError(
                f"Todo stage not found: {normalized_expanded_stage}"
            )

        runtime_kind = self._runtime_kind(todo, normalized_actor_id)
        if runtime_kind is TodoRuntimeKind.LEGACY_CONTEXT:
            return self._legacy_progress(todo, stages)
        if runtime_kind is TodoRuntimeKind.PRODUCT_RUNTIME_PENDING:
            return self._skeleton(
                todo,
                stages,
                current=True,
                status=TodoProgressStageStatus.NOT_STARTED,
                runtime_kind=TodoRuntimeKind.PRODUCT_RUNTIME_PENDING,
            )
        if runtime_kind is TodoRuntimeKind.INVALID_BINDING:
            return self._skeleton(
                todo,
                stages,
                current=True,
                status=TodoProgressStageStatus.UNKNOWN,
                runtime_error_code="invalid_runtime_binding",
                runtime_kind=TodoRuntimeKind.INVALID_BINDING,
            )
        if todo.product_collet_id is None:
            return self._skeleton(
                todo,
                stages,
                current=True,
                status=TodoProgressStageStatus.UNKNOWN,
                runtime_error_code="product_collet_missing",
                runtime_kind=TodoRuntimeKind.INVALID_BINDING,
            )

        context = ActorContext(
            actor=PrincipalRef("human", normalized_actor_id),
            source="todo_product",
        )
        try:
            raw_rollouts = await self._product_profiles.query_collet_rollout(
                todo.product_collet_id,
                True,
                context=context,
            )
            runtime = parse_product_rollouts(raw_rollouts)
            projected = self._project_stages(stages, runtime)
        except Exception:
            return self._skeleton(
                todo,
                stages,
                current=True,
                status=TodoProgressStageStatus.UNKNOWN,
                runtime_error_code="product_progress_unavailable",
                runtime_kind=TodoRuntimeKind.PRODUCT_COLLET,
            )

        selected_stage_code = (
            normalized_expanded_stage
            if normalized_expanded_stage is not None
            else _current_stage_code(projected)
        )
        selected = None
        if selected_stage_code is not None:
            selected = next(
                (
                    stage
                    for stage in runtime.current.stages
                    if stage.name == selected_stage_code
                ),
                None,
            )
        details_failure: str | None = None
        details_loaded: str | None = None
        if selected is not None and selected.session_id:
            try:
                details = await self._product_profiles.query_stage_rollout_details(
                    selected.stage_rollout_id,
                    context=context,
                    limit=limit,
                )
                details_loaded, projected = self._apply_details(
                    details,
                    projected,
                    selected_stage_code or "",
                )
            except Exception:
                details_failure = "product_stage_details_unavailable"
        projected = tuple(
            stage
            if stage.code != selected_stage_code
            else TodoStageProgress(
                **{
                    **stage.__dict__,
                    "details_loaded": details_loaded is not None,
                    "messages_complete": details_complete(details_loaded),
                }
            )
            for stage in projected
        )
        return TodoProgress(
            todo_id=todo.todo_id,
            status=todo.status,
            runtime_kind=runtime_kind,
            runtime_unavailable=False,
            runtime_error_code=details_failure,
            current_stage_code=selected_stage_code,
            rollout_revision=runtime.revision,
            stopped=runtime.stopped,
            stages=projected,
        )

    def _runtime_kind(
        self, todo: TodoRecord, actor_id: str
    ) -> TodoRuntimeKind:
        if not todo.template_id or not todo.template_version_id:
            return resolve_todo_runtime_kind(todo, product_profile_id=None)
        detail = self._bot_teams.get_bot_team(
            bot_team_id=todo.template_id,
            actor_id=actor_id,
        )
        revision = next(
            (
                revision
                for revision in detail.revisions
                if revision.bot_team_revision_id == todo.template_version_id
            ),
            None,
        )
        if revision is None:
            return TodoRuntimeKind.INVALID_BINDING
        return resolve_todo_runtime_kind(
            todo, product_profile_id=revision.product_profile_id
        )

    def _legacy_progress(
        self, todo: TodoRecord, stages: tuple[TodoStageRecord, ...]
    ) -> TodoProgress:
        projected = tuple(
            TodoStageProgress(
                stage_instance_id=stage.stage_instance_id,
                code=stage.stage_key,
                name=stage.stage_name,
                order=stage.stage_order,
                kind=stage.stage_kind,
                status=_local_stage_status(stage.status),
                owner_user_id=stage.owner_user_id,
                owner_user_name=stage.owner_user_name,
                executor_subject_id=stage.executor_subject_id,
                executor_subject_name=stage.executor_subject_name,
                summary=stage.purpose,
                current_step=stage.requested_action,
                blocker=(
                    TodoProgressBlocker(
                        reason=stage.state_reason,
                        requested_action=stage.requested_action,
                        error_code=None,
                    )
                    if stage.pending_action_kind is not None
                    else None
                ),
                updated_at=stage.updated_at.isoformat(),
            )
            for stage in stages
        )
        return TodoProgress(
            todo_id=todo.todo_id,
            status=todo.status,
            runtime_kind=TodoRuntimeKind.LEGACY_CONTEXT,
            runtime_unavailable=False,
            runtime_error_code=None,
            current_stage_code=_current_stage_code(projected),
            stages=projected,
        )

    def _skeleton(
        self,
        todo: TodoRecord,
        stages: tuple[TodoStageRecord, ...],
        *,
        current: bool,
        status: TodoProgressStageStatus,
        runtime_error_code: str | None = None,
        runtime_kind: TodoRuntimeKind | None = None,
    ) -> TodoProgress:
        projected = self._base_stages(stages, status)
        return TodoProgress(
            todo_id=todo.todo_id,
            status=todo.status,
            runtime_kind=runtime_kind or TodoRuntimeKind.LEGACY_CONTEXT,
            runtime_unavailable=bool(runtime_error_code),
            runtime_error_code=runtime_error_code,
            current_stage_code=_current_stage_code(projected) if current else None,
            stages=projected,
        )

    @staticmethod
    def _base_stages(
        stages: tuple[TodoStageRecord, ...], status: TodoProgressStageStatus
    ) -> tuple[TodoStageProgress, ...]:
        return tuple(
            TodoStageProgress(
                stage_instance_id=stage.stage_instance_id,
                code=stage.stage_key,
                name=stage.stage_name,
                order=stage.stage_order,
                kind=stage.stage_kind,
                status=status,
                owner_user_id=stage.owner_user_id,
                owner_user_name=stage.owner_user_name,
                executor_subject_id=stage.executor_subject_id,
                executor_subject_name=stage.executor_subject_name,
                summary=stage.purpose,
                current_step=None,
            )
            for stage in stages
        )

    @staticmethod
    def _project_stages(
        stages: tuple[TodoStageRecord, ...], runtime: ProductRolloutSnapshot
    ) -> tuple[TodoStageProgress, ...]:
        current_by_code: dict[str, StageRollout] = {
            stage.name: stage for stage in runtime.current.stages
        }
        attempt_counts: dict[str, int] = {}
        for rollout in runtime.rollouts:
            for stage in rollout.stages:
                attempt_counts[stage.name] = attempt_counts.get(stage.name, 0) + 1

        projected: list[TodoStageProgress] = []
        for stage in stages:
            runtime_stage = current_by_code.get(stage.stage_key)
            if runtime_stage is None:
                status = TodoProgressStageStatus.UNKNOWN
                summary = stage.purpose
            else:
                try:
                    status = map_product_stage_status(runtime_stage.status)
                except ValueError:
                    status = TodoProgressStageStatus.UNKNOWN
                summary = (
                    _safe_text(runtime_stage.error_msg)
                    or f"阶段状态：{runtime_stage.status.value}"
                )
            projected.append(
                TodoStageProgress(
                    stage_instance_id=stage.stage_instance_id,
                    code=stage.stage_key,
                    name=stage.stage_name,
                    order=stage.stage_order,
                    kind=stage.stage_kind,
                    status=status,
                    owner_user_id=stage.owner_user_id,
                    owner_user_name=stage.owner_user_name,
                    executor_subject_id=stage.executor_subject_id,
                    executor_subject_name=stage.executor_subject_name,
                    summary=summary,
                    current_step=None,
                    outputs=(
                        _stage_outputs(runtime_stage) if runtime_stage is not None else ()
                    ),
                    blocker=(
                        TodoProgressBlocker(
                            reason=_safe_text(runtime_stage.error_msg) or None,
                            requested_action=(
                                "请查看运行失败原因并决定重试或调整"
                                if status is TodoProgressStageStatus.INTERRUPTED
                                else None
                            ),
                            error_code=_safe_text(runtime_stage.error_msg) or None,
                        )
                        if runtime_stage is not None
                        and status
                        in {
                            TodoProgressStageStatus.INTERRUPTED,
                            TodoProgressStageStatus.PENDING_ACTION,
                        }
                        else None
                    ),
                    attempt_count=attempt_counts.get(stage.stage_key, 0),
                    updated_at=stage.updated_at.isoformat(),
                )
            )
        return tuple(projected)

    @staticmethod
    def _apply_details(
        details: StageRolloutDetails,
        stages: tuple[TodoStageProgress, ...],
        stage_code: str,
    ) -> tuple[str | None, tuple[TodoStageProgress, ...]]:
        steps, ignored = _messages_steps(details.messages)
        projected = []
        for stage in stages:
            if stage.code == stage_code:
                current_step = steps[-1].name if steps else None
                summary = current_step or stage.summary
                projected.append(
                    TodoStageProgress(
                        **{
                            **stage.__dict__,
                            "summary": summary,
                            "current_step": current_step,
                            "steps": steps,
                            "ignored_message_count": ignored,
                        }
                    )
                )
            else:
                projected.append(stage)
        return details.complete, tuple(projected)

    @staticmethod
    def _required_text(value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return normalized

    @staticmethod
    def _optional_text(value: object, field: str, max_length: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return normalized


def details_complete(value: bool | None) -> bool | None:
    """Do not claim completeness unless the runtime explicitly said it is complete."""
    return True if value is True else value


def _current_stage_code(stages: tuple[TodoStageProgress, ...]) -> str | None:
    for stage in stages:
        if stage.status not in {
            TodoProgressStageStatus.COMPLETED,
            TodoProgressStageStatus.CANCELLED,
        }:
            return stage.code
    return None
