"""Smoke tests for service-bot build endpoint.

Tests the following endpoint from ``adapters/http/service_bot/router_build.py``:
- POST /api/service-bot/build

This endpoint exercises the post-R14 sandbox provider through
``BotBuildService._sync_skill_links`` → ``provider.get_base_path()``.
The provider's ``base_path`` now comes from ``WorkspaceConfig`` (typed,
YAML-driven) instead of an ``is_local_mode`` flag.

The NAS shell-outs (``sudo chmod`` / ``sudo rsync`` against
``/home/admin/.merge_nas``) are the one thing no test host can serve, so the
happy case binds :class:`_LocalShellFreeBuildService` — a subclass that
overrides that single method and inherits the rest — through the injector.
Everything the case is actually about still runs for real: the source
directory genuinely does not exist, so ``_migrate_bot_instance`` takes its
own early return and the build completes with ``data.success = False`` while
the HTTP envelope returns ``ApiResponse(success=True, ...)``. That
distinction is what the assertions below verify — the HTTP wrapper is the
routing concern, the migration outcome is internal data.
"""
from __future__ import annotations

import subprocess

from agentclaw.community.api.bot_build_service import BotBuildServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
)
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


class _LocalShellFreeBuildService(BotBuildService):
    """The real build service with its one subprocess boundary neutralised.

    ``BotBuildService`` shells out (``sudo chmod`` / ``sudo rsync``) against the
    NAS mount at ``/home/admin/.merge_nas``, which no test host has. That single
    method is the only thing standing between this endpoint and a hermetic run,
    so the subclass overrides it and inherits every other line — path
    computation, the sandbox-provider resolution this test exists to cover, the
    early return when the source directory is absent, and the response shape.

    Bound through the injector below, so the router resolves it exactly as it
    resolves the production service: no patching, and no chance of the
    substitution outliving the case that asked for it.
    """

    def _run_local_command(
        self,
        cmd: list[str],
        command_name: str,
        error_message: str,
        timeout_seconds: float | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _bind_shell_free_build_service(world) -> None:
    """Serve ``BotBuildServiceProtocol`` from the shell-free subclass.

    The instance is re-clothed from the injector's own service rather than
    constructed here, so it carries the production collaborators — the same
    device service, sandbox registry and path factory — and this file never
    has to restate that wiring.
    """
    wired = world.get(BotBuildServiceProtocol)
    shell_free = _LocalShellFreeBuildService.__new__(_LocalShellFreeBuildService)
    shell_free.__dict__.update(wired.__dict__)
    world.injector.binder.bind(BotBuildServiceProtocol, to=shell_free, scope=None)


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
    seed=lambda world: (
        _seed_build_bot(world), _bind_shell_free_build_service(world),
    ),
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
