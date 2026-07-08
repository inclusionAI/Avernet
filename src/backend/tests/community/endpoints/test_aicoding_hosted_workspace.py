"""Endpoint tests for POST /api/aicoding/bot/{bot_id}/dima-workspace.

Covers happy path (workspace created via stub WorkspaceHostingService)
and error path (non-applicationCoding bot → 400 in app envelope).

The ``TestingAicodingModule`` binds ``_StubWorkspaceHostingService`` which
returns ``W_STUB_{bot_id}`` without hitting the real DIMA OpenAPI.
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
    """applicationCoding bot with no dima_space_id."""
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
            "data": {"dima_space_id": "W_STUB_bot_app_coding"},
        },
    ),
)
def create_dima_workspace_ok():
    """Happy path: applicationCoding bot without dima_space_id → stub creates one."""


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
