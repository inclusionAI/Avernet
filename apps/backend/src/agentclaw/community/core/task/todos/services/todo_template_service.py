"""Product policy and authorization for versioned Todo templates."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.task.todo_templates import (
    TodoTemplateRepositoryProtocol,
)
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.errors import TodoTemplateStateConflictError
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateDetail,
    TodoTemplatePage,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
)
from agentclaw.community.core.task.todos.template_management_protocol import (
    TodoTemplateManagementServiceProtocol,
)
from agentclaw.community.core.task.todos.template_models import TodoTemplateDefinition
from agentclaw.community.core.task.todos.template_validation import (
    TodoTemplateDefinitionValidator,
)


class TodoTemplateService(TodoTemplateManagementServiceProtocol):
    """Enforce Space access and the mutable-draft template lifecycle."""

    @inject
    def __init__(
        self,
        repository: TodoTemplateRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._validator = TodoTemplateDefinitionValidator()

    def create_template(
        self,
        *,
        space_id: int,
        actor_id: str,
        definition: TodoTemplateDefinition,
        env: str | None = None,
    ) -> TodoTemplateDetail:
        normalized = self._validator.validate(definition)
        self._require_space_admin(
            space_id=space_id,
            actor_id=actor_id,
            env=env,
        )
        return self._repository.create_template(
            space_id=space_id,
            definition=normalized,
            created_by=actor_id,
        )

    def list_templates(
        self,
        *,
        space_id: int,
        actor_id: str,
        status: TodoTemplateStatus | str | None,
        page: int,
        page_size: int,
        env: str | None = None,
    ) -> TodoTemplatePage:
        normalized_status = self._status(status)
        self._pagination(page, page_size)
        self._space_access.require_space_member(
            space_id=space_id,
            user_id=actor_id,
            env=env,
        )
        return self._repository.list_templates(
            space_id=space_id,
            status=normalized_status,
            page=page,
            page_size=page_size,
        )

    def get_template(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateDetail:
        template = self._repository.get_template(template_id=template_id)
        self._space_access.require_space_member(
            space_id=template.space_id,
            user_id=actor_id,
            env=env,
        )
        return TodoTemplateDetail(
            template=template,
            versions=self._repository.list_versions(template_id=template_id),
        )

    def get_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord:
        template = self._repository.get_template(template_id=template_id)
        self._space_access.require_space_member(
            space_id=template.space_id,
            user_id=actor_id,
            env=env,
        )
        return self._repository.get_version(
            template_id=template_id,
            template_version_id=template_version_id,
        )

    def create_draft(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord:
        template = self._require_admin(
            template_id=template_id, actor_id=actor_id, env=env
        )
        self._require_active(template.status)
        return self._repository.create_draft(
            template_id=template_id,
            created_by=actor_id,
        )

    def update_draft(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        definition: TodoTemplateDefinition,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord:
        normalized = self._validator.validate(definition)
        self._require_admin(template_id=template_id, actor_id=actor_id, env=env)
        return self._repository.update_draft(
            template_id=template_id,
            template_version_id=template_version_id,
            definition=normalized,
            updated_by=actor_id,
        )

    def validate_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord:
        version = self.get_version(
            template_id=template_id,
            template_version_id=template_version_id,
            actor_id=actor_id,
            env=env,
        )
        self._validator.validate(version.definition)
        return version

    def publish_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord:
        template = self._require_admin(
            template_id=template_id, actor_id=actor_id, env=env
        )
        self._require_active(template.status)
        return self._repository.publish_version(
            template_id=template_id,
            template_version_id=template_version_id,
            published_by=actor_id,
        )

    def publish_product_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        product_profile_id: int,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord:
        template = self._require_admin(
            template_id=template_id, actor_id=actor_id, env=env
        )
        self._require_active(template.status)
        if type(product_profile_id) is not int or product_profile_id < 1:
            raise TodoTemplateStateConflictError(
                "product_profile_id must be a positive integer"
            )
        return self._repository.publish_product_version(
            template_id=template_id,
            template_version_id=template_version_id,
            product_profile_id=product_profile_id,
            published_by=actor_id,
        )

    def archive_template(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ):
        self._require_admin(template_id=template_id, actor_id=actor_id, env=env)
        return self._repository.set_template_status(
            template_id=template_id,
            status=TodoTemplateStatus.ARCHIVED,
            updated_by=actor_id,
        )

    def activate_template(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ):
        self._require_admin(template_id=template_id, actor_id=actor_id, env=env)
        return self._repository.set_template_status(
            template_id=template_id,
            status=TodoTemplateStatus.ACTIVE,
            updated_by=actor_id,
        )

    def _require_admin(self, *, template_id: str, actor_id: str, env: str | None):
        template = self._repository.get_template(template_id=template_id)
        self._require_space_admin(
            space_id=template.space_id,
            actor_id=actor_id,
            env=env,
        )
        return template

    def _require_space_admin(
        self, *, space_id: int, actor_id: str, env: str | None
    ) -> None:
        space, member = self._space_access.require_space_member(
            space_id=space_id,
            user_id=actor_id,
            env=env,
        )
        if space.created_by == actor_id:
            return
        if member.role not in (
            SpaceRole.ADMIN,
            SpaceRole.OWNER,
            SpaceRole.ADMINISTRATOR,
        ):
            raise SpaceAccessDeniedError("space administrator role required")

    @staticmethod
    def _require_active(status: TodoTemplateStatus) -> None:
        if status is TodoTemplateStatus.ARCHIVED:
            raise TodoTemplateStateConflictError(
                "archived Todo template cannot change versions"
            )

    @staticmethod
    def _status(value: TodoTemplateStatus | str | None) -> TodoTemplateStatus | None:
        if value is None or isinstance(value, TodoTemplateStatus):
            return value
        try:
            return TodoTemplateStatus(value)
        except ValueError as exc:
            raise TodoTemplateStateConflictError(
                f"unsupported Todo template status {value!r}"
            ) from exc

    @staticmethod
    def _pagination(page: int, page_size: int) -> None:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError(
                "page must be positive and page_size must be between 1 and 100"
            )
