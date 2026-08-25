"""Endpoint tests for POST /api/aicoding/bot/{bot_id}/dima-workspace.

Covers happy path (workspace created via the stub WorkspaceHostingService that
DI binds under the test/singlebox profile → synthetic ``W_STUB_<bot_id>``) and
error path (non-coding bot → 400 in app envelope).
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
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
    """applicationCoding bot with no dima_space_id (pure data seed).

    The bound ``WorkspaceHostingService`` is the test stub, which synthesises a
    deterministic ``W_STUB_<bot_id>`` workspace id, so no DIMA client is
    touched. Seed only writes data through the real repo/template service.
    """
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
    """personal openclaw bot (not Coding Bot) → ensure_hosted_workspace should reject."""
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
        "active_engine": "openclaw",
    })



def _seed_claude_code_bot_without_dima(world):
    """Non-applicationCoding coding-engine bot should be allowed to create DIMA workspace."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": "bot_claude_code",
        "bot_name": "ClaudeCodeBot",
        "owner_id": "u_owner",
        "bot_type": "personal",
        "status": "ACTIVE",
        "entity_id": "u_owner",
        "entity_type": "staff",
        "creator_id": "u_owner",
        "template_type": "normalCC",
        "active_engine": "claude_code",
    })

    template_svc = world.get(TemplateService)
    template_svc.create_template(bot_id="bot_claude_code", template_config={"foo": "bar"})



def _seed_app_coding_bot_with_member_collaborator(world):
    """ApplicationCoding bot with a MEMBER collaborator."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_member")
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": "bot_app_coding_collab",
        "bot_name": "AppBotCollab",
        "owner_id": "u_owner",
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": "u_owner",
        "entity_type": "staff",
        "creator_id": "u_owner",
        "template_type": "applicationCoding",
    })

    template_svc = world.get(TemplateService)
    template_svc.create_template(bot_id="bot_app_coding_collab", template_config={"foo": "bar"})

    from tests.community.factories.bot_collaborator import make_collaborator

    make_collaborator(
        world,
        bot_id="bot_app_coding_collab",
        owner_id="u_owner",
        user_id="u_member",
        role="member",
        operator_id="u_owner",
    )

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
    """Happy path: applicationCoding bot without dima_space_id → stub creates W_STUB_bot_app_coding."""


@endpoint_test(
    method="POST",
    path="/api/aicoding/bot/{bot_id}/dima-workspace",
    scenario="ok_claude_code_non_app_coding",
    input=CaseInput(
        path_params={"bot_id": "bot_claude_code"},
        query_params={"user_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_claude_code_bot_without_dima,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"dima_space_id": "W_STUB_bot_claude_code"},
        },
    ),
)
def create_dima_workspace_ok_for_claude_code_non_app_coding():
    """Happy path: non-applicationCoding claude_code bot can create DIMA workspace."""


@endpoint_test(
    method="POST",
    path="/api/aicoding/bot/{bot_id}/dima-workspace",
    scenario="member_collaborator_allowed",
    input=CaseInput(
        path_params={"bot_id": "bot_app_coding_collab"},
        query_params={"user_id": "u_owner"},
        headers={"x-user-id": "u_member"},
    ),
    seed=_seed_app_coding_bot_with_member_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"dima_space_id": "W_STUB_bot_app_coding_collab"},
        },
    ),
)
def create_dima_workspace_allows_member_collaborator():
    """MEMBER collaborator can create DIMA workspace for the owner's coding bot."""


@endpoint_test(
    method="POST",
    path="/api/aicoding/bot/{bot_id}/dima-workspace",
    scenario="member_collaborator_self_resolved",
    input=CaseInput(
        path_params={"bot_id": "bot_app_coding_collab"},
        query_params={"user_id": "u_member"},
        headers={"x-user-id": "u_member"},
    ),
    seed=_seed_app_coding_bot_with_member_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"dima_space_id": "W_STUB_bot_app_coding_collab"},
        },
    ),
)
def create_dima_workspace_allows_member_collaborator_self_resolved():
    """协作者用自己的 user_id 访问时，也能通过协作者关系解析到真实 owner。"""


@endpoint_test(
    method="POST",
    path="/api/aicoding/bot/{bot_id}/dima-workspace",
    scenario="error_non_coding",
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
    """Error: non-coding bot returns 400 in app envelope."""
