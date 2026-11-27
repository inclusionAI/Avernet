"""Persistence contract for versioned Todo product templates."""

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
class TodoTemplateRepositoryProtocol(Protocol):
    """Persist template identities and immutable published versions."""

    @abstractmethod
    def create_template(
        self,
        *,
        space_id: int,
        definition: TodoTemplateDefinition,
        created_by: str,
    ) -> TodoTemplateDetail: ...

    @abstractmethod
    def get_template(self, *, template_id: str) -> TodoTemplateRecord: ...

    @abstractmethod
    def list_templates(
        self,
        *,
        space_id: int,
        status: TodoTemplateStatus | None,
        page: int,
        page_size: int,
    ) -> TodoTemplatePage: ...

    @abstractmethod
    def list_versions(
        self, *, template_id: str
    ) -> tuple[TodoTemplateVersionRecord, ...]: ...

    @abstractmethod
    def get_version(
        self, *, template_id: str, template_version_id: str
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def create_draft(
        self, *, template_id: str, created_by: str
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def update_draft(
        self,
        *,
        template_id: str,
        template_version_id: str,
        definition: TodoTemplateDefinition,
        updated_by: str,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def publish_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        published_by: str,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def publish_product_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        product_profile_id: int,
        published_by: str,
    ) -> TodoTemplateVersionRecord: ...

    @abstractmethod
    def set_template_status(
        self,
        *,
        template_id: str,
        status: TodoTemplateStatus,
        updated_by: str,
    ) -> TodoTemplateRecord: ...
