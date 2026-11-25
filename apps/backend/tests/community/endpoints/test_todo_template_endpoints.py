"""Per-endpoint coverage for Todo template management and capabilities."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from injector import singleton

from agentclaw.community.api.task.todo_template_capability_service import (
    TodoTemplateCapabilityServiceProtocol,
)
from agentclaw.community.api.task.todo_template_service import (
    TodoTemplateManagementServiceProtocol,
)
from agentclaw.community.core.task.todos.template_capabilities import (
    TODO_TEMPLATE_CAPABILITIES,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateStatus,
    TodoTemplateVersionStatus,
)
from tests.community.endpoints.todo_endpoint_support import (
    OTHER_USER_ID,
    SPACE_ID,
    TEMPLATE_ID,
    USER_ID,
    VERSION_ID,
    enable_public_auth,
    principal_headers,
    template_definition_payload,
    template_detail,
    template_page,
    template_record,
    template_version,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_BASE = "/openapi/v1/collaboration/tasks/todo-templates"
_CAPABILITIES = "/openapi/v1/collaboration/tasks/todo-template-capabilities"
_HEADERS = principal_headers()
_QUERY = {"user_id": USER_ID}
_WRONG_QUERY = {"user_id": OTHER_USER_ID}
_CREATE_BODY = {
    "space_id": SPACE_ID,
    "definition": template_definition_payload(),
}
_UPDATE_BODY = {"definition": template_definition_payload("Updated delivery")}


class _TemplateManagementService:
    def create_template(self, **_kwargs: Any):
        return template_detail()

    def list_templates(self, **_kwargs: Any):
        return template_page()

    def get_template(self, **_kwargs: Any):
        return template_detail()

    def get_version(self, **_kwargs: Any):
        return template_version()

    def create_draft(self, **_kwargs: Any):
        return replace(
            template_version(),
            template_version_id="todo_tplv_2",
            version_no=2,
        )

    def update_draft(self, **kwargs: Any):
        return replace(template_version(), definition=kwargs["definition"])

    def validate_version(self, **_kwargs: Any):
        return template_version()

    def publish_version(self, **_kwargs: Any):
        return template_version(status=TodoTemplateVersionStatus.PUBLISHED)

    def archive_template(self, **_kwargs: Any):
        return template_record(status=TodoTemplateStatus.ARCHIVED)

    def activate_template(self, **_kwargs: Any):
        return template_record(status=TodoTemplateStatus.ACTIVE)


class _TemplateCapabilityService:
    def get_capabilities(self):
        return TODO_TEMPLATE_CAPABILITIES


def _bind_template_world(world: Any) -> None:
    enable_public_auth(world)
    binder = world.injector.binder
    binder.bind(
        TodoTemplateManagementServiceProtocol,
        to=_TemplateManagementService(),
        scope=singleton,
    )
    binder.bind(
        TodoTemplateCapabilityServiceProtocol,
        to=_TemplateCapabilityService(),
        scope=singleton,
    )


@endpoint_test(
    method="GET",
    path=_CAPABILITIES,
    scenario="supported_contract",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "supported_schema_versions": ["1.0"],
                "stage_kinds": ["bot", "human"],
            },
        },
    ),
)
def get_template_capabilities_ok():
    """The editor reads the deterministic template capability contract."""


@endpoint_test(
    method="GET",
    path=_CAPABILITIES,
    scenario="wrong_user",
    input=CaseInput(query_params=_WRONG_QUERY, headers=_HEADERS),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def get_template_capabilities_forbidden():
    """Capabilities remain scoped to the authenticated Human."""


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="create_first_draft",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS, json_body=_CREATE_BODY),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"template": {"template_id": TEMPLATE_ID}},
        },
    ),
)
def create_template_ok():
    """A Space administrator creates a template and its first draft."""


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="wrong_user",
    input=CaseInput(
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
        json_body=_CREATE_BODY,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def create_template_forbidden():
    """A Human cannot create a template for another named user."""


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="list_space_templates",
    input=CaseInput(
        query_params={**_QUERY, "space_id": SPACE_ID},
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"total": 1, "items": [{"template_id": TEMPLATE_ID}]},
        },
    ),
)
def list_templates_ok():
    """A Space member lists visible Todo templates."""


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="wrong_user",
    input=CaseInput(
        query_params={**_WRONG_QUERY, "space_id": SPACE_ID},
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def list_templates_forbidden():
    """Template listing rejects an impersonated user scope."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{template_id}}",
    scenario="get_template",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "template": {"template_id": TEMPLATE_ID},
                "versions": [{"template_version_id": VERSION_ID}],
            },
        },
    ),
)
def get_template_ok():
    """A Space member reads template metadata and versions."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{template_id}}",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def get_template_forbidden():
    """Template detail rejects an impersonated user scope."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}",
    scenario="get_version",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"template_version_id": VERSION_ID, "status": "draft"},
        },
    ),
)
def get_template_version_ok():
    """A Space member reads one exact template version."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def get_template_version_forbidden():
    """Template version detail rejects an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/versions",
    scenario="create_next_draft",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"template_version_id": "todo_tplv_2", "version_no": 2},
        },
    ),
)
def create_template_draft_ok():
    """An administrator creates the next editable draft."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/versions",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def create_template_draft_forbidden():
    """Draft creation rejects an impersonated user scope."""


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}",
    scenario="replace_draft_definition",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_UPDATE_BODY,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"definition": {"name": "Updated delivery"}},
        },
    ),
)
def update_template_draft_ok():
    """An administrator replaces a draft definition."""


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
        json_body=_UPDATE_BODY,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def update_template_draft_forbidden():
    """Draft replacement rejects an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}/validate",
    scenario="validate_draft",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"template_version_id": VERSION_ID, "status": "draft"},
        },
    ),
)
def validate_template_version_ok():
    """A stored draft validates against current capabilities."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}/validate",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def validate_template_version_forbidden():
    """Template validation rejects an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}/publish",
    scenario="publish_validated_draft",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"template_version_id": VERSION_ID, "status": "published"},
        },
    ),
)
def publish_template_version_ok():
    """An administrator publishes a validated draft."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/versions/{{version_id}}/publish",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID, "version_id": VERSION_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def publish_template_version_forbidden():
    """Template publication rejects an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/archive",
    scenario="archive_template",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"template_id": TEMPLATE_ID, "status": "archived"},
        },
    ),
)
def archive_template_ok():
    """An administrator archives a template identity."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/archive",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def archive_template_forbidden():
    """Template archival rejects an impersonated user scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/activate",
    scenario="activate_template",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"template_id": TEMPLATE_ID, "status": "active"},
        },
    ),
)
def activate_template_ok():
    """An administrator reactivates an archived template."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{template_id}}/activate",
    scenario="wrong_user",
    input=CaseInput(
        path_params={"template_id": TEMPLATE_ID},
        query_params=_WRONG_QUERY,
        headers=_HEADERS,
    ),
    seed=_bind_template_world,
    expect=ExpectError(status=403),
)
def activate_template_forbidden():
    """Template activation rejects an impersonated user scope."""
