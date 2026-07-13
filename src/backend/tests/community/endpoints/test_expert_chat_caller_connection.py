"""Endpoint tests for POST /api/v1/expert-chats/caller-connection.

Tests the per-caller BaaS container instance provisioning endpoint
with real database operations and DI injection. Uses the project's
LocalHttpClient.set_override mechanism for HTTP interactions.
"""
from typing import Annotated

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
    http_envelope_response,
)


# Admin user ID (seeded in test config as super_admin)
_ADMIN_USER_ID = "100000"

# Test data tokens
_BOT_ID = "caller_bot_ep"
_BOT_UUID = "BOT-caller-ep-001"
_OWNER_ID = "owner_ep"
_USER_ID = "caller_ep"
_BAAS_PUB_ID = 999


def _baas(world):
    """Get BaaS HTTP client from the injector."""
    return world.get(Annotated[HttpClient, QUALIFIER_BAAS])


def _install_baas(world) -> None:
    """Stub BaaS HTTP interactions via LocalHttpClient's set_override."""
    def _get(path: str, **_kw):
        if "/ws-info" in path:
            return http_envelope_response({
                "ws_url": "ws://localhost:8890/api/openclaw/ws",
                "token": "test-caller-token",
                "target": _BOT_UUID,
                "expires_at": "2099-01-01T00:00:00Z",
                "paas_device_id": "device-ep-001",
                "baas_base_url": "http://localhost:8890",
                "engine_port": 20003,
                "tenant": "test_tenant",
                "bot_uuid": _BOT_UUID,
            })
        if "/progress" in path:
            return http_envelope_response({
                "status": "SUCCESS",
                "device_details": [],
                "overall_progress": {},
                "failed_devices": [],
            })
        return http_envelope_response({})

    def _post(path: str, **_kw):
        if "/api/v1/bots" in path:
            return http_envelope_response({
                "bot_uuid": _BOT_UUID,
                "publish_id": _BAAS_PUB_ID,
            })
        if "/update" in path:
            return http_envelope_response({
                "bot_uuid": _BOT_UUID,
                "publish_id": _BAAS_PUB_ID,
            })
        return http_envelope_response({})

    _baas(world).set_override("get", _get)
    _baas(world).set_override("post", _post)


def _seed_published_service_bot(world) -> None:
    """Seed a published service bot with SUCCESS status."""
    env = get_current_env()
    make_staff_user(world, user_id=_OWNER_ID)

    # Create source binding
    binding_repo = world.get(DeviceBindingRepository)
    src_binding_id = binding_repo.insert_binding(
        entity_id=_OWNER_ID,
        entity_type="staff",
        device_id="SRC-UUID-EP",
        device_provider="teclaw",
        env=env,
        device_props={},
        status="ACTIVE",
        apply_reason="seed",
        applied_by=_OWNER_ID,
    )

    # Create bot
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": _BOT_ID,
        "bot_name": "Caller Bot EP",
        "owner_id": _OWNER_ID,
        "owner_name": "Owner EP",
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": _OWNER_ID,
        "entity_type": "staff",
        "creator_id": _OWNER_ID,
        "active_engine": "teclaw",
        "binding_id": src_binding_id,
    })

    # Create SUCCESS publish record
    publish_repo = world.get(BotPublishRepositoryProtocol)
    publish_repo.insert({
        "source_bot_pk": 1,
        "source_bot_id": _BOT_ID,
        "publish_bot_id": _BOT_ID,
        "name": "Caller Bot EP",
        "owner_id": _OWNER_ID,
        "permission_owner": _OWNER_ID,
        "status": PublishStatus.SUCCESS,
        "version": 1,
        "env": env,
        "ext": {"migration_path": "/nas/migration/path"},
    })


def _seed_happy(world) -> None:
    """Seed for happy path: published bot + BaaS stubs."""
    _seed_published_service_bot(world)
    _install_baas(world)


# ---------------------------------------------------------------------------
# Happy path: super admin successfully gets caller connection
# ---------------------------------------------------------------------------

@endpoint_test(
    method="POST",
    path="/api/v1/expert-chats/caller-connection",
    scenario="super_admin_gets_connection",
    input=CaseInput(
        query_params={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "user_id": _USER_ID,
        },
        headers={"x-user-id": _ADMIN_USER_ID},
    ),
    seed=_seed_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "error_code": 0,
        },
    ),
)
def test_caller_connection_happy():
    """Super admin can get caller connection for another user."""


# ---------------------------------------------------------------------------
# Error path: anonymous user is rejected
# ---------------------------------------------------------------------------

@endpoint_test(
    method="POST",
    path="/api/v1/expert-chats/caller-connection",
    scenario="anonymous_user_rejected",
    input=CaseInput(
        query_params={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "user_id": _USER_ID,
        },
        headers={"x-user-id": "anonymous"},
    ),
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 400,
        },
    ),
)
def test_caller_connection_anonymous():
    """Anonymous user cannot access the endpoint."""


# ---------------------------------------------------------------------------
# Error path: non-super-admin is forbidden
# ---------------------------------------------------------------------------

@endpoint_test(
    method="POST",
    path="/api/v1/expert-chats/caller-connection",
    scenario="non_super_admin_forbidden",
    input=CaseInput(
        query_params={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "user_id": _USER_ID,
        },
        headers={"x-user-id": "200000"},  # Non-admin user
    ),
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 403,
        },
    ),
)
def test_caller_connection_non_admin():
    """Non-super-admin user is forbidden from accessing the endpoint."""


# ---------------------------------------------------------------------------
# Error path: unexpected exception returns generic error
# Lines 265-268 in router.py
# ---------------------------------------------------------------------------

def _seed_with_baas_error(world):
    """Seed with a BaaS that raises unexpected exception."""
    _seed_published_service_bot(world)
    # Override BaaS to raise an unexpected exception
    def _post_with_error(path: str, **_kw):
        raise RuntimeError("Unexpected internal error")
    _baas(world).set_override("post", _post_with_error)


@endpoint_test(
    method="POST",
    path="/api/v1/expert-chats/caller-connection",
    scenario="unexpected_exception_handled",
    input=CaseInput(
        query_params={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "user_id": _USER_ID,
        },
        headers={"x-user-id": _ADMIN_USER_ID},
    ),
    seed=_seed_with_baas_error,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 5999,
        },
    ),
)
def test_caller_connection_unexpected_exception():
    """Unexpected exception in get_caller_connection_for_other returns error 5999.

    This test covers lines 265-268 in router.py: the catch-all exception handler.
    """