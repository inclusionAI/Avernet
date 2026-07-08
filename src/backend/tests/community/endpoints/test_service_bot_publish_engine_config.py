"""Endpoint tests for GET /api/service-bot/publish/{publish_id}/engine-config.

Tests the following endpoint from ``adapters/http/service_bot/router_publish.py``:
- GET /api/service-bot/publish/{publish_id}/engine-config

This endpoint retrieves engine configuration based on publish record status.
The endpoint uses CollaboratorPermissionInterceptor which checks if the user
has permission (owner has automatic access).
"""
from __future__ import annotations

import json

from agentclaw.community.core.service_bot.repository.models import BotPublishModel, PublishStatus
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.factories.access import make_staff_user
from tests.community.factories.devices import make_active_local_device
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


def _seed_publish_record(
    world,
    *,
    publish_id: int,
    owner_id: str,
    status: str,
    build_target_path: str | None = None,
    binding: dict | None = None,
) -> None:
    """Seed a user and a publish record for testing.

    The user ID must match the owner_id for the permission check to pass
    (owner has automatic access).

    Uses BotPublishModel directly to insert with a specific ID (the repository
    insert() method ignores the id field due to autoincrement).
    """
    make_staff_user(world, user_id=owner_id)

    ext: dict = {}
    if build_target_path is not None:
        ext["build_target_path"] = build_target_path
    if binding is not None:
        ext["binding"] = binding
    ext_json = json.dumps(ext, ensure_ascii=False) if ext else None

    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        row = BotPublishModel(
            id=publish_id,
            source_bot_pk=1,
            source_bot_id="bot-test-001",
            publish_bot_id="bot-test-001-pub",
            name="Test Bot",
            owner_id=owner_id,
            status=status,
            version=1,
            permission_owner=owner_id,
            ext=ext_json,
        )
        session.add(row)
        session.flush()


# ============================================================================
# Happy path — resolvable stage binding, config file absent on the device → {}
# (real DI: local binding resolves via resolve_for_binding → LocalDeviceFileSystem
# pathlib mode; the missing config file yields success + empty config).
# ============================================================================


def _seed_publish_with_local_binding(world, *, publish_id: int, owner_id: str) -> None:
    make_staff_user(world, user_id=owner_id)
    bind_id = make_active_local_device(world, owner_id=owner_id, device_id=f"loc_{publish_id}")
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        session.add(BotPublishModel(
            id=publish_id,
            source_bot_pk=1,
            source_bot_id="bot-test-001",
            publish_bot_id="bot-test-001-pub",
            name="Test Bot",
            owner_id=owner_id,
            status=PublishStatus.SUCCESS,
            version=1,
            permission_owner=owner_id,
            ext=json.dumps({"binding": {"online": bind_id}}, ensure_ascii=False),
        ))
        session.flush()


@endpoint_test(
    method="GET",
    path="/api/service-bot/publish/{publish_id}/engine-config",
    scenario="ok_empty_config",
    input=CaseInput(
        path_params={"publish_id": 1000},
        headers={"x-user-id": "u_publish_owner"},
    ),
    seed=lambda world: _seed_publish_with_local_binding(
        world, publish_id=1000, owner_id="u_publish_owner",
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {}},
    ),
)
def get_publish_engine_config_ok_empty():
    """A resolvable stage binding with no config file on the device returns success + {}."""


# ============================================================================
# Validating status with no verify binding → business error (not masked empty)
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/publish/{publish_id}/engine-config",
    scenario="validating_no_binding_surfaces_error",
    input=CaseInput(
        path_params={"publish_id": 1001},
        headers={"x-user-id": "u_publish_owner"},
    ),
    seed=lambda world: _seed_publish_record(
        world,
        publish_id=1001,
        owner_id="u_publish_owner",
        status=PublishStatus.VALIDATING,
        build_target_path="/tmp/test_build",  # ext present but no binding
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_publish_engine_config_validating_no_binding():
    """A validating publish with no verify-stage binding surfaces a business error."""


# ============================================================================
# Error Path - Publish record not found
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/publish/{publish_id}/engine-config",
    scenario="not_found",
    input=CaseInput(
        path_params={"publish_id": 4040},
        headers={"x-user-id": "u_publish_owner"},
    ),
    seed=lambda world: make_staff_user(world, user_id="u_publish_owner"),
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 404,
        },
    ),
)
def get_publish_engine_config_not_found():
    """GET /api/service-bot/publish/{publish_id}/engine-config returns 404 for non-existent publish."""


# ============================================================================
# No stage binding → business error (not masked as empty config)
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/publish/{publish_id}/engine-config",
    scenario="no_stage_binding_surfaces_error",
    input=CaseInput(
        path_params={"publish_id": 1002},
        headers={"x-user-id": "u_publish_owner"},
    ),
    seed=lambda world: _seed_publish_record(
        world,
        publish_id=1002,
        owner_id="u_publish_owner",
        status=PublishStatus.SUCCESS,
        binding={},  # no online/verify → no stage bind_id
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_publish_engine_config_no_stage_binding():
    """A publish with no active-stage binding surfaces a business error (not empty config)."""


# ============================================================================
# Unresolvable stage binding (real failure) → surfaced as a business error,
# NOT masked as an empty config. End-to-end through real DI: a bogus bind_id
# makes DeviceContextResolver.resolve_for_binding raise, which propagates.
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/publish/{publish_id}/engine-config",
    scenario="unresolvable_binding_surfaces_error",
    input=CaseInput(
        path_params={"publish_id": 1003},
        headers={"x-user-id": "u_publish_owner"},
    ),
    seed=lambda world: _seed_publish_record(
        world,
        publish_id=1003,
        owner_id="u_publish_owner",
        status=PublishStatus.SUCCESS,
        binding={"online": 9999999},  # binding row does not exist → resolve raises
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_publish_engine_config_unresolvable_binding():
    """An unresolvable stage binding surfaces a business error, not an empty config."""