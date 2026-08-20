"""Public Editors endpoint contract tests."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.editors.router import (
    add_editor,
    leave_editors,
    list_editors,
    remove_editor,
    update_editor,
)
from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.editors.schemas import (
    EditorCreate,
    EditorUpdate,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
from agentclaw.community.core.repository.protocols.bot import (
    CollaboratorRepositoryProtocol,
)
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client
from tests.community.factories.bot_collaborator import make_bot


def _request(method: str = "GET") -> Request:
    request = Request({"type": "http", "method": method, "path": "/"})
    request.state.trace_id = "trace-editors"
    return request


def _record(role: str = "member") -> CollaboratorRecord:
    now = datetime(2026, 8, 19, 10, 0, 0)
    return CollaboratorRecord(
        id=7,
        bot_pk=10,
        bot_id="bot-1",
        owner_id="owner-1",
        user_id="member-1",
        user_name="Member",
        role=role,
        operator_id="owner-1",
        env="dev",
        gmt_create=now,
        gmt_modified=now,
    )


@pytest.mark.asyncio
async def test_list_editors_projects_public_fields_and_forwards_scope():
    service = Mock()
    service.list_editors.return_value = [_record()]

    response = await list_editors(
        bot_id="bot-1",
        request=_request(),
        actor_id="member-1",
        owner_id="owner-1",
        role=None,
        service=service,
    )

    assert response.request_id == "trace-editors"
    assert response.data.total == 1
    assert response.data.items[0].model_dump(mode="json") == {
        "id": 7,
        "user_id": "member-1",
        "user_name": "Member",
        "role": "member",
        "created_at": "2026-08-19T10:00:00",
        "updated_at": "2026-08-19T10:00:00",
    }
    service.list_editors.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        user_id="member-1",
        role=None,
    )


@pytest.mark.asyncio
async def test_mutations_delegate_to_bot_first_service_methods():
    service = Mock()
    service.add_editor.return_value = _record()
    service.update_editor.return_value = _record("admin")

    created = await add_editor(
        bot_id="bot-1",
        body=EditorCreate(editor_user_id="member-1", role="member"),
        request=_request("POST"),
        actor_id="owner-1",
        owner_id="owner-1",
        service=service,
    )
    updated = await update_editor(
        bot_id="bot-1",
        editor_id=7,
        body=EditorUpdate(role="admin"),
        request=_request("PATCH"),
        actor_id="owner-1",
        owner_id="owner-1",
        service=service,
    )
    removed = await remove_editor(
        bot_id="bot-1",
        editor_id=7,
        request=_request("DELETE"),
        actor_id="owner-1",
        owner_id="owner-1",
        service=service,
    )
    left = await leave_editors(
        bot_id="bot-1",
        request=_request("DELETE"),
        actor_id="member-1",
        owner_id="owner-1",
        service=service,
    )

    assert created.code == 201000
    assert updated.data.role.value == "admin"
    assert removed.data.deleted is True
    assert left.data.deleted is True
    service.add_editor.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        user_id="member-1",
        operator_id="owner-1",
        user_name=None,
        role="member",
    )
    service.update_editor.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        collaborator_id=7,
        operator_id="owner-1",
        role="admin",
    )


def test_editor_requests_reject_unknown_fields_and_roles():
    with pytest.raises(ValidationError):
        EditorCreate(editor_user_id="u-1", role="owner")
    with pytest.raises(ValidationError):
        EditorUpdate(role="member", user_id="unexpected")


def test_editor_operations_admit_app_callers_with_an_addressed_bot_grant():
    collection = "/openapi/v1/bots/{bot_id}/editors"
    item = f"{collection}/{{editor_id}}"

    assert ADMISSION[("GET", collection)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    assert ADMISSION[("POST", collection)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    assert ADMISSION[("PATCH", item)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    assert ADMISSION[("DELETE", item)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    assert (
        ADMISSION[("DELETE", f"{collection}/me")]
        is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    )


def test_assembled_app_resolves_editor_service_and_space_access_protocol(
    app_with_testing_modules, world
):
    """Smoke the real DI graph, not only a handler supplied with a Mock."""
    bot = make_bot(world, bot_id="bot-editors", owner_id="owner-1", bot_type="service")
    world.get(CollaboratorRepositoryProtocol).insert(
        {
            "bot_pk": bot["id"],
            "bot_id": "bot-editors",
            "owner_id": "owner-1",
            "user_id": "member-1",
            "role": "member",
            "operator_id": "owner-1",
        }
    )
    app_with_testing_modules.dependency_overrides[require_principal] = lambda: {
        "user_id": "owner-1"
    }
    try:
        client = user_scoped_client(app_with_testing_modules, "owner-1")
        response = client.get("/openapi/v1/bots/bot-editors/editors")
    finally:
        app_with_testing_modules.dependency_overrides.pop(require_principal, None)

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["items"][0]["user_id"] == "member-1"
