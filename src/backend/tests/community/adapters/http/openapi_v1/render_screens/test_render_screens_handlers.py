"""Public render-screen endpoint and authorization tests."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    Check,
    NoCheck,
)
from agentclaw.community.adapters.http.openapi_v1.render_screens.gating import (
    require_scoped_record,
)
from agentclaw.community.adapters.http.openapi_v1.render_screens.router import (
    create_render_screen,
    delete_render_screen,
    list_render_screens,
    update_render_screen,
)
from agentclaw.community.adapters.http.openapi_v1.render_screens.schemas import (
    RenderScreenCreate,
    RenderScreenUpdate,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_management.render_screen.errors import (
    RenderScreenNotFoundError,
)
from agentclaw.community.core.bot_management.render_screen.models import (
    RenderScreenRecord,
)
from agentclaw.community.core.bot_management.render_screen.sqlite_models import (
    RenderScreenModel,
)
from agentclaw.community.core.repository.protocols.bot import RenderScreenRepository
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client
from tests.community.factories.bot_collaborator import make_bot


def _request(method: str = "GET") -> Request:
    request = Request({"type": "http", "method": method, "path": "/"})
    request.state.trace_id = "trace-render-screens"
    return request


def _bot(**overrides):
    return {
        "id": 10,
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "space_id": "22",
        "bot_type": "service",
        **overrides,
    }


def _record(**overrides) -> RenderScreenRecord:
    now = datetime(2026, 8, 19, 10, 0, 0)
    values = {
        "id": 7,
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "name": "dashboard",
        "cdn_url": "https://cdn.example.com/dashboard.js",
        "env": "dev",
        "creator_id": "member-1",
        "is_delete": 0,
        "gmt_create": now,
        "gmt_modified": now,
        **overrides,
    }
    return RenderScreenRecord(**values)


@pytest.mark.asyncio
async def test_list_is_readable_without_editor_permission():
    bots = Mock()
    bots.get_bot.return_value = _bot()
    service = Mock()
    service.list_render_screens.return_value = [_record()]

    response = await list_render_screens(
        bot_id="bot-1",
        request=_request(),
        actor_id="viewer-1",
        owner_id="owner-1",
        bot_service=bots,
        service=service,
    )

    assert response.request_id == "trace-render-screens"
    assert response.data.total == 1
    assert response.data.items[0].model_dump(mode="json") == {
        "id": 7,
        "name": "dashboard",
        "cdn_url": "https://cdn.example.com/dashboard.js",
        "creator_id": "member-1",
        "created_at": "2026-08-19T10:00:00",
        "updated_at": "2026-08-19T10:00:00",
    }
    bots.get_bot.assert_called_once_with("bot-1", "owner-1")
    service.list_render_screens.assert_called_once_with(
        bot_id="bot-1", owner_id="owner-1"
    )


@pytest.mark.asyncio
async def test_member_editor_can_create_update_and_delete():
    bots = Mock()
    bots.get_bot.return_value = _bot()
    # No collaborator double: the handlers no longer resolve a level themselves.
    # The seam adjudicates Check(MEMBER) before they run, and these three assert
    # what the handler does *once admitted*.
    created_record = _record()
    updated_record = _record(
        name="dashboard-v2",
        cdn_url="https://cdn.example.com/dashboard-v2.js",
    )
    service = Mock()
    service.create_render_screen.return_value = 7
    service.get_render_screen.side_effect = [
        created_record,
        created_record,
        updated_record,
        updated_record,
    ]

    created_response = await create_render_screen(
        bot_id="bot-1",
        body=RenderScreenCreate(
            name="dashboard",
            cdn_url="https://cdn.example.com/dashboard.js",
        ),
        request=_request("POST"),
        actor_id="member-1",
        owner_id="owner-1",
        bot_service=bots,
        service=service,
    )
    updated_response = await update_render_screen(
        bot_id="bot-1",
        render_screen_id=7,
        body=RenderScreenUpdate(
            name="dashboard-v2",
            cdn_url="https://cdn.example.com/dashboard-v2.js",
        ),
        request=_request("PATCH"),
        actor_id="member-1",
        owner_id="owner-1",
        bot_service=bots,
        service=service,
    )
    deleted_response = await delete_render_screen(
        bot_id="bot-1",
        render_screen_id=7,
        request=_request("DELETE"),
        actor_id="member-1",
        owner_id="owner-1",
        bot_service=bots,
        service=service,
    )

    assert created_response.code == 201000
    assert updated_response.data.name == "dashboard-v2"
    assert deleted_response.data.deleted is True
    service.create_render_screen.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        name="dashboard",
        cdn_url="https://cdn.example.com/dashboard.js",
        creator_id="member-1",
    )
    service.update_render_screen.assert_called_once_with(
        record_id=7,
        name="dashboard-v2",
        cdn_url="https://cdn.example.com/dashboard-v2.js",
    )
    service.delete_render_screen.assert_called_once_with(record_id=7)


def test_removed_team_editor_cannot_mutate():
    """The bar that kept a removed editor out, now stated where it is enforced.

    This used to drive ``require_editable_bot`` directly and assert it refused a
    caller at ``PermissionLevel.NONE``. That helper is gone: the three mutating
    operations declare ``Check(MEMBER)`` and the seam adjudicates them before
    the handler runs, so the refusal itself is ``bot_access``'s and is covered
    by ``test_bot_access.py``.

    What is render-screens' own to assert is that the three operations really
    carry that bar — and that the read does not, since group and share viewers
    hold no Editor relation and must still reach the CDN mapping.
    """
    mutations = [
        ("POST", "/openapi/v1/bots/{bot_id}/render-screens"),
        ("PATCH", "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}"),
    ]
    for key in mutations:
        rule = AUTHORIZATION[key]
        assert isinstance(rule, Check), f"{key[0]} {key[1]} is no longer adjudicated"
        assert rule.level is PermissionLevel.MEMBER, (
            f"{key[0]} {key[1]} moved off the MEMBER bar require_editable_bot "
            "enforced before the seam took it over"
        )

    read = AUTHORIZATION[("GET", "/openapi/v1/bots/{bot_id}/render-screens")]
    assert isinstance(read, NoCheck), (
        "the read must stay unadjudicated — share and group viewers hold no "
        "Editor relation and still need the mapping to render panels"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"bot_id": "other-bot"},
        {"owner_id": "other-owner"},
        {"env": "pre"},
    ],
)
def test_record_id_is_bound_to_bot_owner_and_environment(overrides):
    service = Mock()
    service.get_render_screen.return_value = _record(**overrides)

    with pytest.raises(RenderScreenNotFoundError):
        require_scoped_record(
            service,
            record_id=7,
            bot_id="bot-1",
            owner_id="owner-1",
        )


def test_requests_reject_unknown_fields_and_non_http_urls():
    with pytest.raises(ValidationError):
        RenderScreenCreate(
            name="dashboard",
            cdn_url="javascript:alert(1)",
        )
    with pytest.raises(ValidationError):
        RenderScreenUpdate(
            name="dashboard",
            cdn_url="https://cdn.example.com/dashboard.js",
            bot_id="unexpected",
        )


def test_admission_allows_all_operations_for_app_callers_with_a_bot_grant():
    collection = "/openapi/v1/bots/{bot_id}/render-screens"
    item = f"{collection}/{{render_screen_id}}"

    assert ADMISSION[("GET", collection)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    assert ADMISSION[("POST", collection)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    assert ADMISSION[("PATCH", item)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    assert ADMISSION[("DELETE", item)] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT


def test_assembled_app_resolves_render_screen_services(app_with_testing_modules, world):
    make_bot(world, bot_id="bot-render-screen", owner_id="owner-1")
    repository = world.get(RenderScreenRepository)
    with repository._db.orm_session() as session:
        RenderScreenModel.__table__.create(bind=session.get_bind(), checkfirst=True)
    repository.insert(
        bot_id="bot-render-screen",
        owner_id="owner-1",
        name="dashboard",
        cdn_url="https://cdn.example.com/dashboard.js",
        creator_id="owner-1",
    )
    app_with_testing_modules.dependency_overrides[require_principal] = lambda: {
        "user_id": "owner-1"
    }
    try:
        client = user_scoped_client(app_with_testing_modules, "owner-1")
        response = client.get("/openapi/v1/bots/bot-render-screen/render-screens")
    finally:
        app_with_testing_modules.dependency_overrides.pop(require_principal, None)

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["items"][0]["name"] == "dashboard"
