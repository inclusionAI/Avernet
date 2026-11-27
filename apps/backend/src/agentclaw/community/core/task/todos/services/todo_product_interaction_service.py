"""Send and recover Todo-owned Product Runtime interactions."""

from __future__ import annotations

import asyncio

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.task.todo_product_interactions import (
    TodoProductInteractionRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.todos.bot_team_service_protocol import (
    TodoBotTeamServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import (
    TodoNotFoundError,
    TodoProductInvalidRuntimeBindingError,
    TodoProductRuntimeNotReadyError,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACTOR_ID_LENGTH,
    MAX_TODO_ID_LENGTH,
    TodoRecord,
)
from agentclaw.community.core.task.todos.product_creation_models import (
    TodoRuntimeKind,
    resolve_todo_runtime_kind,
)
from agentclaw.community.core.task.todos.product_interaction_models import (
    MAX_PRODUCT_INTERACTION_ID_LENGTH,
    MAX_PRODUCT_INTERACTION_MESSAGE_LENGTH,
    TodoProductInteraction,
    TodoProductInteractionKind,
    TodoProductInteractionPage,
    build_product_interaction_id,
    build_stop_request_id,
)
from agentclaw.community.core.task.todos.product_interaction_protocol import (
    TodoProductInteractionServiceProtocol,
)
from agentclaw.community.core.task.todos.services.todo_product_progress_service import (
    parse_product_rollouts,
)
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.core.task.todos.stage_outcome_protocol import (
    TodoStageServiceProtocol,
)

_PRODUCT_SOURCE = "todo_product"
_STOP_MESSAGE = "请停止当前待办执行。"


class TodoProductInteractionService(TodoProductInteractionServiceProtocol):
    """Keep local interaction facts authoritative at the product boundary."""

    @inject
    def __init__(
        self,
        todos: TodoServiceProtocol,
        bot_teams: TodoBotTeamServiceProtocol,
        product_profiles: ProductProfileServiceProtocol,
        repository: TodoProductInteractionRepositoryProtocol,
        stages: TodoStageServiceProtocol,
    ) -> None:
        self._todos = todos
        self._bot_teams = bot_teams
        self._product_profiles = product_profiles
        self._repository = repository
        self._stages = stages

    async def list_interactions(
        self,
        *,
        todo_id: str,
        actor_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> TodoProductInteractionPage:
        normalized_todo = self._required(todo_id, "todo_id", MAX_TODO_ID_LENGTH)
        normalized_actor = self._required(actor_id, "actor_id", MAX_ACTOR_ID_LENGTH)
        self._validate_page(page, page_size)
        todo = await asyncio.to_thread(
            self._todos.get_todo,
            todo_id=normalized_todo,
            actor_id=normalized_actor,
        )
        self._require_product_runtime(todo, normalized_actor)
        items = await asyncio.to_thread(
            self._repository.list_interactions,
            todo_id=normalized_todo,
            page=page,
            page_size=page_size,
        )
        blocking = await asyncio.to_thread(
            self._repository.get_blocking_interaction,
            todo_id=normalized_todo,
        )
        return TodoProductInteractionPage(
            items=items,
            can_send=blocking is None,
            blocking_interaction_id=blocking.interaction_id if blocking else None,
        )

    async def send_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        message: str,
        reply_to_interaction_id: str | None = None,
    ) -> TodoProductInteraction:
        normalized_todo = self._required(todo_id, "todo_id", MAX_TODO_ID_LENGTH)
        normalized_actor = self._required(actor_id, "actor_id", MAX_ACTOR_ID_LENGTH)
        normalized_message = self._required(
            message,
            "message",
            MAX_PRODUCT_INTERACTION_MESSAGE_LENGTH,
        )
        normalized_reply = self._optional(
            reply_to_interaction_id,
            "reply_to_interaction_id",
            MAX_PRODUCT_INTERACTION_ID_LENGTH,
        )
        todo = await asyncio.to_thread(
            self._todos.get_todo,
            todo_id=normalized_todo,
            actor_id=normalized_actor,
        )
        self._require_product_runtime(todo, normalized_actor)
        interaction_id = build_product_interaction_id()
        interaction = await asyncio.to_thread(
            self._repository.create_interaction_single_flight,
            todo_id=normalized_todo,
            interaction_id=interaction_id,
            kind=TodoProductInteractionKind.HUMAN_MESSAGE,
            actor_id=normalized_actor,
            actor_name=None,
            message=normalized_message,
            product_collet_id=self._collet_id(todo),
            reply_to_interaction_id=normalized_reply,
        )
        return await self._deliver(interaction, normalized_actor)

    async def cancel_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        interaction_id: str,
    ) -> TodoProductInteraction:
        normalized_todo = self._required(todo_id, "todo_id", MAX_TODO_ID_LENGTH)
        normalized_actor = self._required(actor_id, "actor_id", MAX_ACTOR_ID_LENGTH)
        normalized_interaction = self._required(
            interaction_id, "interaction_id", MAX_PRODUCT_INTERACTION_ID_LENGTH
        )
        todo = await asyncio.to_thread(
            self._todos.get_todo,
            todo_id=normalized_todo,
            actor_id=normalized_actor,
        )
        self._require_product_runtime(todo, normalized_actor)
        stop_id = build_stop_request_id(normalized_interaction)
        interaction = await asyncio.to_thread(
            self._repository.request_cancel_and_create_stop,
            todo_id=normalized_todo,
            interaction_id=normalized_interaction,
            stop_interaction_id=stop_id,
            actor_id=normalized_actor,
            actor_name=None,
            message=_STOP_MESSAGE,
            product_collet_id=self._collet_id(todo),
        )
        if interaction.delivery_state.value != "sending":
            return interaction
        return await self._deliver(interaction, normalized_actor, stop_target=True)

    async def _deliver(
        self,
        interaction: TodoProductInteraction,
        actor_id: str,
        *,
        stop_target: bool = False,
    ) -> TodoProductInteraction:
        context = ActorContext(
            actor=PrincipalRef("human", actor_id),
            source=_PRODUCT_SOURCE,
        )
        try:
            raw_rollouts = await self._product_profiles.query_collet_rollout(
                interaction.product_collet_id,
                True,
                context=context,
            )
            runtime = parse_product_rollouts(raw_rollouts)
        except Exception:
            return await self._fail(interaction, "product_runtime_unavailable")

        interaction = await asyncio.to_thread(
            self._repository.save_rollout_fence,
            interaction_id=interaction.interaction_id,
            collet_rollout_id=runtime.current_rollout_id,
            expected_revision=runtime.revision,
        )

        try:
            result = await self._product_profiles.interactive(
                runtime.current_rollout_id,
                interaction.request_message,
                context=context,
                request_id=interaction.interaction_id,
                expected_revision=runtime.revision,
            )
        except ContextCollabVersionConflict:
            return await self._fail(interaction, "stale_rollout_revision")
        except (ConnectionError, TimeoutError, asyncio.TimeoutError):
            return await self._unknown(interaction, "interaction_delivery_unknown")
        except Exception:
            return await self._fail(interaction, "product_interaction_unavailable")

        try:
            adjust = AdjustTypeEnum(result.type)
        except (TypeError, ValueError):
            return await self._fail(interaction, "product_interaction_invalid")

        completed = await asyncio.to_thread(
            self._repository.complete_interaction,
            interaction_id=interaction.interaction_id,
            response_type=adjust.value,
            response_message=self._safe_response(result.message),
        )
        if adjust is AdjustTypeEnum.SHUTDOWN:
            if stop_target:
                await asyncio.to_thread(
                    self._repository.mark_cancelled,
                    todo_id=interaction.todo_id,
                    interaction_id=self._stop_target_id(interaction.interaction_id),
                )
            await self._cancel_todo(interaction, actor_id)
        return completed

    async def _fail(
        self, interaction: TodoProductInteraction, failure_code: str
    ) -> TodoProductInteraction:
        return await asyncio.to_thread(
            self._repository.fail_interaction,
            interaction_id=interaction.interaction_id,
            failure_code=failure_code,
        )

    async def _unknown(
        self, interaction: TodoProductInteraction, failure_code: str
    ) -> TodoProductInteraction:
        return await asyncio.to_thread(
            self._repository.mark_delivery_unknown,
            interaction_id=interaction.interaction_id,
            failure_code=failure_code,
        )

    async def _cancel_todo(
        self, interaction: TodoProductInteraction, actor_id: str
    ) -> None:
        await asyncio.to_thread(
            self._stages.cancel,
            todo_id=interaction.todo_id,
            actor_id=actor_id,
            reason="Product runtime confirmed shutdown",
        )

    def _require_product_runtime(
        self, todo: TodoRecord, actor_id: str
    ) -> None:
        if not todo.template_id or not todo.template_version_id:
            raise TodoProductInvalidRuntimeBindingError()
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
            raise TodoProductInvalidRuntimeBindingError()
        runtime = resolve_todo_runtime_kind(
            todo, product_profile_id=revision.product_profile_id
        )
        if runtime is TodoRuntimeKind.PRODUCT_RUNTIME_PENDING:
            raise TodoProductRuntimeNotReadyError()
        if runtime is TodoRuntimeKind.LEGACY_CONTEXT:
            raise TodoProductInvalidRuntimeBindingError()
        if runtime is TodoRuntimeKind.INVALID_BINDING:
            raise TodoProductInvalidRuntimeBindingError()
        if todo.product_collet_id is None:
            raise TodoProductRuntimeNotReadyError()

    @staticmethod
    def _collet_id(todo: TodoRecord) -> int:
        if todo.product_collet_id is None:
            raise TodoProductRuntimeNotReadyError()
        return int(todo.product_collet_id)

    @staticmethod
    def _stop_target_id(stop_interaction_id: str) -> str:
        suffix = ":shutdown:v1"
        if stop_interaction_id.endswith(suffix):
            return stop_interaction_id[: -len(suffix)]
        raise TodoNotFoundError("invalid Product stop identity")

    @staticmethod
    def _safe_response(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()[:MAX_PRODUCT_INTERACTION_MESSAGE_LENGTH]

    @staticmethod
    def _validate_page(page: int, page_size: int) -> None:
        if type(page) is not int or page <= 0:
            raise ValidationError("page must be a positive integer")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValidationError("page_size must be an integer in 1..100")

    @staticmethod
    def _required(value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return normalized

    @staticmethod
    def _optional(value: object, field: str, max_length: int) -> str | None:
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
