"""Endpoint tests for POST /api/aicoding/bot/{bot_id}/dima-workspace.

Covers happy path (workspace created via stub WorkspaceHostingService)
and error path (non-applicationCoding bot → 400 in app envelope).

TEMP 本地改动：DI 现在绑真实 ``WorkspaceHostingService``，所以 seed 里对真实
``WorkspaceHostingClient``（DI 单例）的对外方法打 mock，走真实 service 联动逻辑但离线。
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ── Seed helpers ───────────────────────────────────────────────────────────


def _seed_app_coding_bot_without_dima(world):
    """applicationCoding bot with no dima_space_id + mock DIMA client methods.

    TEMP 本地改动：DI 现在绑真实 WorkspaceHostingService（非 stub），所以这里
    对真实 WorkspaceHostingClient（DI 单例，与 service 共享）的对外方法打 mock，
    避免打 localhost:9999 — 走真实 service 联动逻辑但仍离线。
    """
    from unittest.mock import MagicMock
    from agentclaw.community.core.bot_management.services.workspace_hosting_client import (
        WorkspaceHostingClient,
    )

    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": "bot_app_coding",
        "bot_name": "AppBot",
        "owner_id": "u_owner",
        "bot_type": "personal",
        "status": "ACTIVE",
        "entity_id": "u_owner",
        "entity_type": "staff",
        "creator_id": "u_owner",
        "template_type": "applicationCoding",
    })

    template_svc = world.get(TemplateService)
    template_svc.create_template(bot_id="bot_app_coding", template_config={"foo": "bar"})

    # mock 真实 client（DI 单例）的对外方法，与 service 联动逻辑配合
    client = world.get(WorkspaceHostingClient)
    client.query_staff_department = MagicMock(return_value="D9999")
    client.create_workspace = MagicMock(return_value={
        "success": True,
        "data": {"workspaceId": "WS_APP_CODING"},
    })
    client.add_admin_members = MagicMock(return_value={"success": True})


def _seed_non_app_coding_bot(world):
    """personal bot (not applicationCoding) → ensure_hosted_workspace should reject."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": "bot_personal",
        "bot_name": "PersonalBot",
        "owner_id": "u_owner",
        "bot_type": "personal",
        "status": "ACTIVE",
        "entity_id": "u_owner",
        "entity_type": "staff",
        "creator_id": "u_owner",
        "template_type": "personal",
    })


# ── Cases ──────────────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/api/aicoding/bot/{bot_id}/dima-workspace",
    scenario="ok",
    input=CaseInput(
        path_params={"bot_id": "bot_app_coding"},
        query_params={"user_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_app_coding_bot_without_dima,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"dima_space_id": "WS_APP_CODING"},
        },
    ),
)
def create_dima_workspace_ok():
    """Happy path: applicationCoding bot without dima_space_id → DIMA creates WS_APP_CODING (mocked client)."""


@endpoint_test(
    method="POST",
    path="/api/aicoding/bot/{bot_id}/dima-workspace",
    scenario="error_non_app_coding",
    input=CaseInput(
        path_params={"bot_id": "bot_personal"},
        query_params={"user_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_non_app_coding_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def create_dima_workspace_error():
    """Error: non-applicationCoding bot returns 400 in app envelope."""
