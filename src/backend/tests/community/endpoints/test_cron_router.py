"""Tests for Cron Router — collaborator permission and owner_id handling.

Tests the following scenarios:
- _resolve_user_identity helper function
- extract_cron_body_params helper function
- All cron endpoints with owner_id parameter
- Permission checks via CollaboratorPermissionInterceptor
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.bot_collaborator.interceptor.extractors import PermissionParams
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot, make_collaborator
from tests.community.factories.devices import make_active_local_device
from tests.community.framework import (
    CaseInput,
    ExpectSuccess,
    ExpectError,
    endpoint_test,
)


# =============================================================================
# Test _resolve_user_identity
# =============================================================================

class TestResolveUserIdentity:
    """Tests for _resolve_user_identity helper function."""

    def test_owner_scenario_returns_current_user(self):
        """When owner_id is None, returns current user's identity."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u001",
            staffId="u001",
            nickName="Test User",
            operatorName="Test User",
        )
        bot_service = MagicMock()
        user_id, nick_name = _resolve_user_identity(None, "bot1", user, bot_service)

        assert user_id == "u001"
        assert nick_name == "Test User"
        # bot_service should not be called when owner_id is None
        bot_service.get_bot.assert_not_called()

    def test_owner_scenario_with_empty_owner_id(self):
        """When owner_id is empty string, returns current user's identity."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u001",
            staffId="u001",
            nickName="Test User",
            operatorName="Test User",
        )
        bot_service = MagicMock()
        user_id, nick_name = _resolve_user_identity("", "bot1", user, bot_service)

        assert user_id == "u001"
        assert nick_name == "Test User"
        bot_service.get_bot.assert_not_called()

    def test_bot_id_all_returns_current_user(self):
        """When bot_id is 'all', returns current user's identity regardless of owner_id."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u001",
            staffId="u001",
            nickName="Test User",
            operatorName="Test User",
        )
        bot_service = MagicMock()
        # Even with owner_id set, bot_id="all" should use current user
        user_id, nick_name = _resolve_user_identity("u_owner", "all", user, bot_service)

        assert user_id == "u001"
        assert nick_name == "Test User"
        bot_service.get_bot.assert_not_called()

    def test_collaborator_scenario_returns_owner_identity(self):
        """When owner_id is set, returns owner's identity from bot info."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u_collab",
            staffId="u_collab",
            nickName="Collab User",
            operatorName="Collab User",
        )

        bot_service = MagicMock()
        bot_service.get_bot.return_value = {
            "owner_id": "u_owner",
            "owner_name": "Owner User",
        }

        user_id, nick_name = _resolve_user_identity("u_owner", "bot1", user, bot_service)

        assert user_id == "u_owner"
        assert nick_name == "Owner User"
        bot_service.get_bot.assert_called_once_with("bot1", "u_owner")

    def test_collaborator_scenario_bot_not_found(self):
        """When bot is not found, falls back to owner_id."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u_collab",
            staffId="u_collab",
            nickName="Collab User",
            operatorName="Collab User",
        )

        bot_service = MagicMock()
        bot_service.get_bot.side_effect = Exception("Bot not found")

        user_id, nick_name = _resolve_user_identity("u_owner", "bot1", user, bot_service)

        # Falls back to owner_id when bot lookup fails
        assert user_id == "u_owner"
        assert nick_name == "u_owner"

    def test_collaborator_scenario_missing_owner_name(self):
        """When bot has no owner_name, falls back to owner_id."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u_collab",
            staffId="u_collab",
            nickName="Collab User",
            operatorName="Collab User",
        )

        bot_service = MagicMock()
        bot_service.get_bot.return_value = {
            "owner_id": "u_owner",
            # No owner_name
        }

        user_id, nick_name = _resolve_user_identity("u_owner", "bot1", user, bot_service)

        assert user_id == "u_owner"
        assert nick_name == "u_owner"


# =============================================================================
# Test extract_cron_body_params
# =============================================================================

class TestExtractCronBodyParams:
    """Tests for extract_cron_body_params helper function."""

    @pytest.mark.asyncio
    async def test_extracts_bot_id_and_owner_id(self):
        """Extracts bot_id and owner_id from request body."""
        from agentclaw.community.adapters.http.cron.router import extract_cron_body_params

        # Mock context with request
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "bot_id": "bot123",
            "owner_id": "u_owner",
            "name": "test cron",
        })

        ctx = MagicMock()
        ctx.route_kwargs = {"request": mock_request}

        result = await extract_cron_body_params(ctx)

        assert isinstance(result, PermissionParams)
        assert result.bot_id == "bot123"
        assert result.owner_id == "u_owner"

    @pytest.mark.asyncio
    async def test_handles_missing_owner_id(self):
        """Handles missing owner_id in request body."""
        from agentclaw.community.adapters.http.cron.router import extract_cron_body_params

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "bot_id": "bot123",
            "name": "test cron",
        })

        ctx = MagicMock()
        ctx.route_kwargs = {"request": mock_request}

        result = await extract_cron_body_params(ctx)

        assert result.bot_id == "bot123"
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_handles_missing_request(self):
        """Returns empty params when request is missing."""
        from agentclaw.community.adapters.http.cron.router import extract_cron_body_params

        ctx = MagicMock()
        ctx.route_kwargs = {}

        result = await extract_cron_body_params(ctx)

        assert result.bot_id is None
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_handles_json_parse_error(self):
        """Returns empty params when JSON parsing fails."""
        from agentclaw.community.adapters.http.cron.router import extract_cron_body_params

        mock_request = MagicMock()
        mock_request.json = AsyncMock(side_effect=Exception("Invalid JSON"))

        ctx = MagicMock()
        ctx.route_kwargs = {"request": mock_request}

        result = await extract_cron_body_params(ctx)

        assert result.bot_id is None
        assert result.owner_id is None


# =============================================================================
# Integration Tests for Cron Router Endpoints
# =============================================================================

def _seed_user(world):
    """Seed a user for testing."""
    make_staff_user(world, user_id="u001")


def _seed_bot_with_owner(world):
    """Seed a bot with owner + an ACTIVE local device so the real relay
    resolves a connection to the in-memory adapter."""
    make_staff_user(world, user_id="u_owner")
    binding_id = make_active_local_device(world, owner_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", owner_name="Owner User", bot_type="service", status="ACTIVE", binding_id=binding_id)


def _seed_bot_with_collaborator(world):
    """Seed a bot with owner + ACTIVE device and a collaborator.

    Also acquires a lock for the collaborator so they can perform operations.
    """
    from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService

    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_collab")
    binding_id = make_active_local_device(world, owner_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", owner_name="Owner User", bot_type="service", status="ACTIVE", binding_id=binding_id)
    make_collaborator(world, bot_id="bot_test", owner_id="u_owner", user_id="u_collab", role="admin", operator_id="u_owner")
    # Acquire lock for collaborator
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_collab")


# ============================================================================
# GET /api/cron - list_crons
# ============================================================================

@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="list_all_bots",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        query_params={"bot_id": "all"},
    ),
    seed=_seed_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": []},
    ),
)
def list_crons_all_bots():
    """list_crons with bot_id=all returns success."""


@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="list_specific_bot",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def list_crons_specific_bot():
    """list_crons with specific bot_id returns success."""


@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="list_with_owner_id_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
    ),
    seed=_seed_bot_with_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def list_crons_collaborator():
    """list_crons with owner_id for collaborator scenario."""


# ============================================================================
# GET /api/cron/status - get_cron_status
# ============================================================================

@endpoint_test(
    method="GET",
    path="/api/cron/status",
    scenario="get_status",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_cron_status_success():
    """get_cron_status returns success."""


# ============================================================================
# GET /api/cron/running - get_running_crons
# ============================================================================

@endpoint_test(
    method="GET",
    path="/api/cron/running",
    scenario="get_running_rejects_all",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        query_params={"bot_id": "all"},
    ),
    seed=_seed_user,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def get_running_crons_rejects_all():
    """get_running_crons requires a specific bot_id."""


# ============================================================================
# GET /api/cron/{task_id} - get_cron
# ============================================================================

@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}",
    scenario="get_cron_by_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_cron_by_id_success():
    """get_cron returns success."""


# ============================================================================
# POST /api/cron - create_cron
# ============================================================================

@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="create_cron_success",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "name": "test cron",
            "schedule": "0 * * * *",
            "command": "echo hello",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def create_cron_success():
    """create_cron returns success."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="create_cron_missing_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "name": "test cron",
            "schedule": "0 * * * *",
            "command": "echo hello",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def create_cron_missing_bot_id():
    """create_cron returns error when bot_id is missing."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="create_cron_with_kind",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "name": "cron with kind",
            "schedule": "0 * * * *",
            "command": "echo hello",
            "kind": "autoInitiate",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def create_cron_with_kind():
    """create_cron forwards explicit kind field to adapter."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="create_cron_with_owner_id",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "name": "test cron",
            "schedule": "0 * * * *",
            "command": "echo hello",
        },
    ),
    seed=_seed_bot_with_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def create_cron_collaborator():
    """create_cron with owner_id for collaborator scenario."""


# ============================================================================
# PUT /api/cron/{task_id} - update_cron
# ============================================================================

@endpoint_test(
    method="PUT",
    path="/api/cron/{task_id}",
    scenario="update_cron",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_001"},
        json_body={
            "name": "updated cron",
            "schedule": "*/5 * * * *",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def update_cron_success():
    """update_cron returns success."""


@endpoint_test(
    method="PUT",
    path="/api/cron/{task_id}",
    scenario="update_cron_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        path_params={"task_id": "task_001"},
        json_body={
            "name": "updated cron",
            "schedule": "*/5 * * * *",
        },
    ),
    seed=_seed_bot_with_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def update_cron_collaborator():
    """update_cron with owner_id for collaborator scenario."""


# ============================================================================
# DELETE /api/cron/{task_id} - delete_cron
# ============================================================================

@endpoint_test(
    method="DELETE",
    path="/api/cron/{task_id}",
    scenario="delete_cron",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def delete_cron_success():
    """delete_cron returns success."""


@endpoint_test(
    method="DELETE",
    path="/api/cron/{task_id}",
    scenario="delete_cron_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def delete_cron_collaborator():
    """delete_cron with owner_id for collaborator scenario."""


# ============================================================================
# POST /api/cron/{task_id}/run - run_cron
# ============================================================================

@endpoint_test(
    method="POST",
    path="/api/cron/{task_id}/run",
    scenario="run_cron",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def run_cron_success():
    """run_cron returns success."""


@endpoint_test(
    method="POST",
    path="/api/cron/{task_id}/run",
    scenario="run_cron_with_force",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "force": "true"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def run_cron_with_force():
    """run_cron with force parameter."""


@endpoint_test(
    method="POST",
    path="/api/cron/{task_id}/run",
    scenario="run_cron_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def run_cron_collaborator():
    """run_cron with owner_id for collaborator scenario."""


# ============================================================================
# GET /api/cron/{task_id}/runs - get_cron_runs
# ============================================================================

@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="get_cron_runs",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_cron_runs_success():
    """get_cron_runs returns success."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="get_cron_runs_with_limit",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "limit": "50"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_cron_runs_with_limit():
    """get_cron_runs with limit parameter."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="get_cron_runs_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        path_params={"task_id": "task_001"},
    ),
    seed=_seed_bot_with_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_cron_runs_collaborator():
    """get_cron_runs with owner_id for collaborator scenario."""


# =============================================================================
# Edge Cases
# =============================================================================

class TestResolveUserIdentityEdgeCases:
    """Tests for edge cases in _resolve_user_identity."""

    def test_resolve_user_identity_with_none_nick_name(self):
        """Handles user with None nick_name."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u001",
            staffId="u001",
            nickName=None,  # None nickName
            operatorName="Test User",
        )
        bot_service = MagicMock()
        user_id, nick_name = _resolve_user_identity(None, "bot1", user, bot_service)

        assert user_id == "u001"
        assert nick_name == "u001"  # Falls back to staffId

    def test_resolve_user_identity_bot_returns_none(self):
        """Handles bot service returning None."""
        from agentclaw.community.adapters.http.cron.router import _resolve_user_identity

        user = AuthenticatedUser(
            id="u_collab",
            staffId="u_collab",
            nickName="Collab User",
            operatorName="Collab User",
        )

        bot_service = MagicMock()
        bot_service.get_bot.return_value = None

        user_id, nick_name = _resolve_user_identity("u_owner", "bot1", user, bot_service)

        # Falls back to owner_id when bot is None
        assert user_id == "u_owner"
        assert nick_name == "u_owner"


# =============================================================================
# Error Handling Tests - Service Errors
# =============================================================================

def _seed_user_for_error(world):
    """Seed a user for error tests."""
    make_staff_user(world, user_id="u001")


def _seed_bot_for_error(world):
    """Seed a bot + ACTIVE device (used by the success-path cases below)."""
    make_staff_user(world, user_id="u_owner")
    binding_id = make_active_local_device(world, owner_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", owner_name="Owner User", bot_type="service", status="ACTIVE", binding_id=binding_id)





@endpoint_test(
    method="GET",
    path="/api/cron/status",
    scenario="get_status_owner",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_status_owner_success():
    """get_cron_status with owner user."""


@endpoint_test(
    method="GET",
    path="/api/cron/running",
    scenario="get_running_owner",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "runtime_stage": "draft"},
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_running_owner_success():
    """get_running_crons with owner user."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}",
    scenario="get_cron_detail",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_detail"},
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_cron_detail_success():
    """get_cron returns task detail."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="create_with_timezone",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "name": "cron with timezone",
            "schedule": "0 * * * *",
            "command": "echo test",
            "timezone": "America/New_York",
            "enabled": False,
            "timeout_secs": 3600,
            "model": "gpt-4",
            "notify": {"email": "test@example.com"},
        },
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def create_cron_with_all_options():
    """create_cron with all optional fields."""


@endpoint_test(
    method="PUT",
    path="/api/cron/{task_id}",
    scenario="update_partial",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_partial"},
        json_body={
            "name": "partially updated",
        },
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def update_cron_partial():
    """update_cron with partial data."""


@endpoint_test(
    method="DELETE",
    path="/api/cron/{task_id}",
    scenario="delete_owner",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_delete"},
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def delete_cron_owner():
    """delete_cron with owner."""


@endpoint_test(
    method="POST",
    path="/api/cron/{task_id}/run",
    scenario="run_no_force",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "force": "false"},
        path_params={"task_id": "task_run"},
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def run_cron_no_force():
    """run_cron with force=false."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="get_runs_default_limit",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_runs"},
    ),
    seed=_seed_bot_for_error,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def get_cron_runs_default_limit():
    """get_cron_runs with default limit."""


# =============================================================================
# Error Scenario Tests
# =============================================================================
# These exercise a REAL error path: a bot with no device binding makes the
# relay raise ("Bot ... has no device binding"), which the router maps to a
# success=False / error_code=500 envelope. No artificial error flag.
#
# The list endpoint remains resilient for bot_id=all. The running endpoint
# requires a specific bot_id and validates its runtime scope.

def _seed_bot_no_device(world):
    """Seed a bot WITHOUT a device binding so forward_request raises."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", owner_name="Owner User", bot_type="service", status="ACTIVE")


def _seed_user_no_bot(world):
    """Seed only a user — a request for a specific bot_id will 404 in
    get_bot, which the relay propagates and the router maps to a 500."""
    make_staff_user(world, user_id="u001")


@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="list_crons_error_missing_bot",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        query_params={"bot_id": "ghost_bot"},
    ),
    seed=_seed_user_no_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def list_crons_error():
    """list_crons surfaces a BotNotFoundError as an error envelope."""


@endpoint_test(
    method="GET",
    path="/api/cron/running",
    scenario="get_running_crons_error_missing_bot",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        query_params={"bot_id": "ghost_bot"},
    ),
    seed=_seed_user_no_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_running_crons_error():
    """get_running_crons surfaces a BotNotFoundError as an error envelope."""


@endpoint_test(
    method="GET",
    path="/api/cron/status",
    scenario="get_cron_status_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
    ),
    seed=_seed_bot_no_device,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_cron_status_error():
    """get_cron_status surfaces the no-device error."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}",
    scenario="get_cron_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_error"},
    ),
    seed=_seed_bot_no_device,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_cron_error():
    """get_cron handles service error."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="create_cron_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "name": "test cron",
            "schedule": "0 * * * *",
            "command": "echo hello",
        },
    ),
    seed=_seed_bot_no_device,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def create_cron_error():
    """create_cron handles service error."""


@endpoint_test(
    method="PUT",
    path="/api/cron/{task_id}",
    scenario="update_cron_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_error"},
        json_body={"name": "updated"},
    ),
    seed=_seed_bot_no_device,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def update_cron_error():
    """update_cron handles service error."""


@endpoint_test(
    method="DELETE",
    path="/api/cron/{task_id}",
    scenario="delete_cron_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_error"},
    ),
    seed=_seed_bot_no_device,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def delete_cron_error():
    """delete_cron handles service error."""


@endpoint_test(
    method="POST",
    path="/api/cron/{task_id}/run",
    scenario="run_cron_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_error"},
    ),
    seed=_seed_bot_no_device,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def run_cron_error():
    """run_cron handles service error."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="get_cron_runs_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test"},
        path_params={"task_id": "task_error"},
    ),
    seed=_seed_bot_no_device,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_cron_runs_error():
    """get_cron_runs handles service error."""
