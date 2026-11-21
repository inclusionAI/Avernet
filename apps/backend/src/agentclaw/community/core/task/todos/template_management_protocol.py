"""Service contract for versioned Todo template management."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.template_management_models import (
        TodoTemplateDetail,
        TodoTemplatePage,
        TodoTemplateRecord,
        TodoTemplateStatus,
        TodoTemplateVersionRecord,
    )
    from agentclaw.community.core.task.todos.template_models import (
        TodoTemplateDefinition,
    )


@runtime_checkable
class TodoTemplateManagementServiceProtocol(Protocol):
    """Authorize and orchestrate Todo template lifecycle operations."""

    @abstractmethod
    def create_template(
        self,
        *,
        space_id: int,
        actor_id: str,
        definition: TodoTemplateDefinition,
        env: str | None = None,
    ) -> TodoTemplateDetail: ...

    @abstractmethod
    def list_templates(
        self,
        *,
        space_id: int,
        actor_id: str,
        status: TodoTemplateStatus | str | None,
        page: int,
        page_size: int,
        env: str | None = None,
    ) -> TodoTemplatePage: ...

    @abstractmethod
    def get_template(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateDetail: ...

    @abstractmethod
    def get_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def create_draft(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def update_draft(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        definition: TodoTemplateDefinition,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def validate_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def publish_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def archive_template(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateRecord: ...

    @abstractmethod
    def activate_template(
        self,
        *,
        template_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> TodoTemplateRecord: ...
