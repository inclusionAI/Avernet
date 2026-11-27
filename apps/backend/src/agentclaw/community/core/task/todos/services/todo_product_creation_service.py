"""Create product Todos through either a single Bot or a Bot team."""

from __future__ import annotations

import asyncio

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.todos.bot_team_models import BotTeamStatus
from agentclaw.community.core.task.todos.bot_team_service_protocol import (
    TodoBotTeamServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import (
    TodoSourceAlreadyImportedError,
    TodoStateConflictError,
    TodoTemplateStateConflictError,
    TodoTemplateValidationError,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACTOR_ID_LENGTH,
    MAX_ACTOR_NAME_LENGTH,
    MAX_BOT_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    TodoRecord,
)
from agentclaw.community.core.task.todos.product_creation_models import (
    EnsureProductColletResult,
    TodoExecutorKind,
    TodoExecutorSelection,
    TodoOwnerInput,
    build_collet_request_id,
    build_product_collet_content,
    resolve_todo_runtime_kind,
    TodoRuntimeKind,
    MAX_DIMA_IMPORT_BATCH_SIZE,
    TodoDimaImportItem,
    TodoDimaImportResult,
    TodoDimaImportStatus,
    TodoProductColletStatus,
)
from agentclaw.community.core.task.todos.product_creation_protocol import (
    TodoProductCreationServiceProtocol,
)
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.core.task.todos.source_models import TodoSourceInput
from agentclaw.community.core.task.todos.stage_models import (
    TodoParticipantBindingInput,
)
from agentclaw.community.core.task.todos.template_creation_protocol import (
    TodoTemplateCreationServiceProtocol,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

_MAX_BOT_TEAM_ID_LENGTH = 64
_MAX_BOT_TEAM_REVISION_ID_LENGTH = 64
_OWNER_BINDING_KEY = "todo_owner"


class TodoProductCreationService(TodoProductCreationServiceProtocol):
    """Map product executor language onto the existing Todo/Template services."""

    @inject
    def __init__(
        self,
        todos: TodoServiceProtocol,
        bot_teams: TodoBotTeamServiceProtocol,
        template_creation: TodoTemplateCreationServiceProtocol,
        product_profiles: ProductProfileServiceProtocol,
        repository: TodoRepositoryProtocol,
    ) -> None:
        self._todos = todos
        self._bot_teams = bot_teams
        self._template_creation = template_creation
        self._product_profiles = product_profiles
        self._repository = repository

    def create_todo(
        self,
        *,
        space_id: int,
        actor_id: str,
        title: str,
        description: str,
        executor: TodoExecutorSelection,
        owner: TodoOwnerInput,
        source: TodoSourceInput | None = None,
        env: str | None = None,
    ) -> TodoRecord:
        normalized_title = self._required_text(title, "title", MAX_TITLE_LENGTH)
        normalized_description = self._text_allow_empty(
            description,
            "description",
            MAX_DESCRIPTION_LENGTH,
        )
        normalized_owner = self._owner(owner)
        executor_kind = self._executor_kind(executor)

        if executor_kind is TodoExecutorKind.BOT:
            if executor.revision_id is not None:
                raise ValidationError("revision_id is not allowed for a Bot executor")
            return self._todos.create_todo(
                space_id=space_id,
                actor_id=actor_id,
                title=normalized_title,
                description=normalized_description,
                bot_id=self._required_text(
                    executor.id, "executor id", MAX_BOT_ID_LENGTH
                ),
                owner=normalized_owner,
                source=source,
                env=env,
            )

        bot_team_id = self._required_text(
            executor.id,
            "executor id",
            _MAX_BOT_TEAM_ID_LENGTH,
        )
        revision_id = self._required_text(
            executor.revision_id,
            "revision_id",
            _MAX_BOT_TEAM_REVISION_ID_LENGTH,
        )
        detail = self._bot_teams.get_bot_team(
            bot_team_id=bot_team_id,
            actor_id=actor_id,
            env=env,
        )
        if detail.bot_team.space_id != space_id:
            raise TodoTemplateValidationError("Bot team does not belong to space")
        if detail.bot_team.status is not BotTeamStatus.ACTIVE:
            raise TodoTemplateStateConflictError(
                "archived Bot team cannot create Todos"
            )
        if not any(
            revision.bot_team_revision_id == revision_id
            for revision in detail.revisions
        ):
            raise TodoTemplateValidationError(
                "revision does not belong to the selected Bot team"
            )
        return self._template_creation.create_todo_from_template(
            space_id=space_id,
            actor_id=actor_id,
            template_id=bot_team_id,
            template_version_id=revision_id,
            title=normalized_title,
            description=normalized_description,
            bindings={
                _OWNER_BINDING_KEY: TodoParticipantBindingInput(
                    subject_type="human",
                    subject_id=normalized_owner.user_id,
                    subject_name=normalized_owner.user_name,
                )
            },
            source=source,
            env=env,
        )

    async def import_dima_todos(
        self,
        *,
        space_id: int,
        actor_id: str,
        executor: TodoExecutorSelection,
        owner: TodoOwnerInput,
        items: tuple[TodoDimaImportItem, ...],
        env: str | None = None,
    ) -> tuple[TodoDimaImportResult, ...]:
        normalized_items = self._dima_import_items(items)
        results: list[TodoDimaImportResult] = []
        for item in normalized_items:
            results.append(
                await self._import_one_dima_item(
                    space_id=space_id,
                    actor_id=actor_id,
                    executor=executor,
                    owner=owner,
                    item=item,
                    env=env,
                )
            )
        return tuple(results)

    async def _import_one_dima_item(
        self,
        *,
        space_id: int,
        actor_id: str,
        executor: TodoExecutorSelection,
        owner: TodoOwnerInput,
        item: TodoDimaImportItem,
        env: str | None,
    ) -> TodoDimaImportResult:
        status = TodoDimaImportStatus.IMPORTED
        todo_id: str | None = None
        try:
            todo = await asyncio.to_thread(
                self.create_todo,
                space_id=space_id,
                actor_id=actor_id,
                title=item.title,
                description=item.description,
                executor=executor,
                owner=owner,
                source=item.source,
                env=env,
            )
            todo_id = todo.todo_id
        except TodoSourceAlreadyImportedError as exc:
            status = TodoDimaImportStatus.DUPLICATE
            todo_id = exc.existing_todo_id
        except Exception:
            return TodoDimaImportResult(
                source_work_item_id=item.source.item_id,
                status=TodoDimaImportStatus.FAILED,
                todo_id=None,
                product_runtime_status=TodoProductColletStatus.RETRYABLE_FAILURE,
                error_code="dima_import_failed",
                retryable=True,
            )

        if todo_id is None:
            return TodoDimaImportResult(
                source_work_item_id=item.source.item_id,
                status=TodoDimaImportStatus.FAILED,
                todo_id=None,
                product_runtime_status=TodoProductColletStatus.INVALID_BINDING,
                error_code="todo_id_missing",
                retryable=True,
            )
        runtime = await self.ensure_product_collet(
            todo_id=todo_id,
            actor_id=actor_id,
            env=env,
        )
        if runtime.status not in {
            TodoProductColletStatus.READY,
            TodoProductColletStatus.NOT_APPLICABLE,
        }:
            return TodoDimaImportResult(
                source_work_item_id=item.source.item_id,
                status=TodoDimaImportStatus.FAILED,
                todo_id=todo_id,
                product_runtime_status=runtime.status,
                error_code=runtime.error_code or "product_runtime_unavailable",
                retryable=(runtime.status is TodoProductColletStatus.RETRYABLE_FAILURE),
            )
        return TodoDimaImportResult(
            source_work_item_id=item.source.item_id,
            status=status,
            todo_id=todo_id,
            product_runtime_status=runtime.status,
        )

    @staticmethod
    def _dima_import_items(
        items: tuple[TodoDimaImportItem, ...],
    ) -> tuple[TodoDimaImportItem, ...]:
        if not isinstance(items, tuple):
            raise ValidationError("dima import items must be a tuple")
        if not 1 <= len(items) <= MAX_DIMA_IMPORT_BATCH_SIZE:
            raise ValidationError(
                "dima import items must contain between 1 and "
                f"{MAX_DIMA_IMPORT_BATCH_SIZE} items"
            )
        normalized: list[TodoDimaImportItem] = []
        for item in items:
            if not isinstance(item, TodoDimaImportItem):
                raise ValidationError("dima import item is invalid")
            if (
                not isinstance(item.source, TodoSourceInput)
                or item.source.source_type != "dima"
            ):
                raise ValidationError("dima import item source must be a Dima source")
            normalized.append(item)
        return tuple(normalized)

    async def ensure_product_collet(
        self,
        *,
        todo_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> EnsureProductColletResult:
        normalized_todo_id = self._required_text(todo_id, "todo_id", 64)
        normalized_actor_id = self._required_text(
            actor_id, "actor_id", MAX_ACTOR_ID_LENGTH
        )
        todo = await asyncio.to_thread(
            self._repository.get_todo,
            todo_id=normalized_todo_id,
        )
        if not todo.template_id or not todo.template_version_id:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.NOT_APPLICABLE,
                todo=todo,
            )

        detail = await asyncio.to_thread(
            self._bot_teams.get_bot_team,
            bot_team_id=todo.template_id,
            actor_id=normalized_actor_id,
            env=env,
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
            return EnsureProductColletResult(
                status=TodoProductColletStatus.INVALID_BINDING,
                todo=todo,
                error_code="revision_not_found",
            )
        runtime_kind = resolve_todo_runtime_kind(
            todo,
            product_profile_id=revision.product_profile_id,
        )
        if runtime_kind is TodoRuntimeKind.PRODUCT_COLLET:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.READY,
                todo=todo,
            )
        if runtime_kind is TodoRuntimeKind.INVALID_BINDING:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.INVALID_BINDING,
                todo=todo,
                error_code="invalid_runtime_binding",
            )
        if runtime_kind is not TodoRuntimeKind.PRODUCT_RUNTIME_PENDING:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.NOT_APPLICABLE,
                todo=todo,
            )
        if revision.product_profile_id is None:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.NOT_APPLICABLE,
                todo=todo,
            )

        stages = await asyncio.to_thread(
            self._repository.list_stages,
            todo_id=normalized_todo_id,
        )
        source = await asyncio.to_thread(
            self._repository.get_source_binding,
            todo_id=normalized_todo_id,
        )
        content = build_product_collet_content(
            todo=todo,
            source=source,
            stages=stages,
            product_profile_id=revision.product_profile_id,
        )
        request_id = build_collet_request_id(
            get_current_avernet_tenant(),
            get_current_env(),
            normalized_todo_id,
        )
        try:
            collet = await self._product_profiles.create_product_collet(
                revision.product_profile_id,
                content,
                context=ActorContext(
                    actor=PrincipalRef("human", normalized_actor_id),
                    source="todo_product",
                ),
                request_id=request_id,
            )
        except Exception:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.RETRYABLE_FAILURE,
                todo=todo,
                error_code="product_runtime_unavailable",
            )
        if collet.profile_id != revision.product_profile_id:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.BINDING_CONFLICT,
                todo=todo,
                error_code="product_profile_mismatch",
            )
        try:
            bound = await asyncio.to_thread(
                self._repository.bind_product_collet,
                todo_id=normalized_todo_id,
                product_collet_id=collet.id,
            )
        except TodoStateConflictError:
            return EnsureProductColletResult(
                status=TodoProductColletStatus.BINDING_CONFLICT,
                todo=await asyncio.to_thread(
                    self._repository.get_todo,
                    todo_id=normalized_todo_id,
                ),
                error_code="product_collet_conflict",
            )
        return EnsureProductColletResult(
            status=TodoProductColletStatus.READY,
            todo=bound,
        )

    @staticmethod
    def _executor_kind(executor: object) -> TodoExecutorKind:
        if not isinstance(executor, TodoExecutorSelection):
            raise ValidationError("executor must be a TodoExecutorSelection")
        try:
            return TodoExecutorKind(executor.type)
        except (TypeError, ValueError) as exc:
            raise ValidationError("executor type is invalid") from exc

    @classmethod
    def _owner(cls, owner: object) -> TodoOwnerInput:
        if not isinstance(owner, TodoOwnerInput):
            raise ValidationError("owner must be a TodoOwnerInput")
        return TodoOwnerInput(
            user_id=cls._required_text(
                owner.user_id,
                "owner user_id",
                MAX_ACTOR_ID_LENGTH,
            ),
            user_name=cls._required_text(
                owner.user_name,
                "owner user_name",
                MAX_ACTOR_NAME_LENGTH,
            ),
        )

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
    def _text_allow_empty(value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if len(normalized) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return normalized
