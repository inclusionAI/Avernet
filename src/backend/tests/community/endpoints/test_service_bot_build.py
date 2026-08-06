"""Smoke tests for service-bot build endpoint.

Tests the following endpoint from ``adapters/http/service_bot/router_build.py``:
- POST /api/service-bot/build

This endpoint exercises the engine-aware build plan selected by the sandbox
provider. The provider's workspace configuration is typed and YAML-driven.

In the test environment the rsync subprocess does not actually run:
``_migrate_bot_instance`` early-returns ``False`` when the source
directory does not exist (which it never does in CI/local-SQLite
mode), so the build completes cleanly with ``data.success = False``
while the HTTP envelope returns ``ApiResponse(success=True, ...)``.
That distinction is what the assertions below verify — the HTTP
wrapper is the routing concern, the migration outcome is internal data.
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Test Setup
# ============================================================================


def _seed_build_bot(world):
    """Seed bot owner + an active service bot with openclaw engine."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert(
        {
            "bot_id": "bot_build",
            "bot_name": "Bot build",
            "owner_id": "u_owner",
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": "u_owner",
            "entity_type": "user",
            "creator_id": "u_owner",
            "active_engine": "openclaw",
        }
    )


def _patch_build_test_seams() -> None:
    """Patch NAS migration side effects for endpoint smoke tests only."""
    from unittest.mock import patch

    patch(
        "agentclaw.community.core.service_bot.services.bot_build_service.BotBuildService._run_local_command",
        return_value="",
    ).start()
    patch("pathlib.Path.exists", return_value=False).start()


# ============================================================================
# Authentication / Authorization
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/build",
    scenario="anonymous_user",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        json_body={"bot_id": "bot_build"},
    ),
    seed=_seed_build_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def build_bot_anonymous():
    """POST /api/service-bot/build with anonymous staff_id returns success=False + error_code=400."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/build",
    scenario="missing_user_header",
    input=CaseInput(
        headers={},  # No x-user-id header
        json_body={"bot_id": "bot_build"},
    ),
    seed=_seed_build_bot,
    expect=ExpectError(
        status=401,  # FastAPI auth middleware returns 401
    ),
)
def build_bot_missing_user():
    """POST /api/service-bot/build without user header returns 401."""


# ============================================================================
# Parameter Validation
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/build",
    scenario="missing_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={},  # No bot_id
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=422,  # FastAPI validation error
    ),
)
def build_bot_missing_bot_id():
    """POST /api/service-bot/build without bot_id returns 422 validation error."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/build",
    scenario="empty_body",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body=None,
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=422,
    ),
)
def build_bot_empty_body():
    """POST /api/service-bot/build with no body returns 422 validation error."""


# ============================================================================
# Bot State
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/build",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={"bot_id": "bot_notexist"},
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        # bot_service.get_bot raises "Bot not found: ..." which the router
        # catches as a generic Exception → success=False, error_code=500
        # (router-side handling, not a 404 path).
        json_contains={"success": False, "error_code": 500},
    ),
)
def build_bot_not_found():
    """POST /api/service-bot/build with non-existent bot returns success=False + error_code=500."""


# ============================================================================
# Happy Path
# ============================================================================
#
# The "happy" case for this endpoint is: HTTP envelope succeeds and the
# inner build envelope reports the bot back. The actual rsync no-ops in
# tests because the source directory does not exist (`_migrate_bot_instance`
# early-returns False). What we're asserting is that the router wires the
# request to BotBuildService, the service resolves the sandbox provider
# via DI (post-R14: WorkspaceConfig-injected), computes paths, and
# returns a well-formed envelope.


@endpoint_test(
    method="POST",
    path="/api/service-bot/build",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={"bot_id": "bot_build", "version": "v1"},
    ),
    seed=lambda world: (_seed_build_bot(world), _patch_build_test_seams()),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "bot_id": "bot_build",
                "entity_id": "u_owner",
                "version": "v1",
            },
        },
    ),
)
def build_bot_ok():
    """POST /api/service-bot/build with valid request returns success=True envelope."""
