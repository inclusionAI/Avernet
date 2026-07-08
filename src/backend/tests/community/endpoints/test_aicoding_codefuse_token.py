"""Endpoint tests for PUT /api/aicoding/bots/{bot_id}/codefuse/auth.

Full BaaS/Arca integration tests live in tests/api/aicoding/test_token_save.py.
These registration-only cases satisfy the coverage gate (happy + error).
"""
from __future__ import annotations

import base64
import json
from typing import Annotated

import httpx

from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER = "u_smoke"
_BOT_ID = "bot_token_test"

# Valid base64 auth_code: {"t":"a"*32,"w":"u_smoke"}
_VALID_AUTH_CODE = base64.b64encode(
    json.dumps({"t": "a" * 32, "w": _OWNER}).encode()
).decode()


def _baas_http(world):
    # BaasService uses QUALIFIER_BAAS (base_url=baas gateway) for control-plane calls.
    # exec_command_on_bot is a control-plane call → goes through QUALIFIER_BAAS.
    return world.get(Annotated[HttpClient, QUALIFIER_BAAS])


def _exec_cmd_response(path, **kwargs):
    """Build a fake httpx.Response for exec_command_on_bot."""
    return httpx.Response(
        status_code=200,
        json={"code": 0, "data": {"exit_code": 0, "stdout": "", "stderr": ""}},
        request=httpx.Request("POST", path),
    )


def _seed_owner_with_bot_and_baas_binding(world):
    """Seed owner, bot, and a BaaS device binding with bot_uuid."""
    make_staff_user(world, user_id=_OWNER)
    repo = world.get(DeviceBindingRepository)
    binding_id = repo.insert_binding(
        entity_id=_OWNER,
        entity_type="staff",
        device_id=f"staff_{_OWNER}_{_BOT_ID}_abc",
        device_provider="baas",
        env="dev",
        device_props={"bot_uuid": "BOT-UUID-FAKE"},
        status="ACTIVE",
        apply_reason="test seed",
        applied_by=_OWNER,
    )
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER, bot_type="personal", binding_id=binding_id)
    _baas_http(world).set_override("post", _exec_cmd_response)


def _seed_owner_only(world):
    make_staff_user(world, user_id=_OWNER)


@endpoint_test(
    method="PUT",
    path="/api/aicoding/bots/{bot_id}/codefuse/auth",
    scenario="ok_baas",
    seed=_seed_owner_with_bot_and_baas_binding,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        json_body={"token": _VALID_AUTH_CODE},
        headers={"x-user-id": _OWNER},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "bot_id": _BOT_ID},
    ),
)
def test_codefuse_token_baas_ok():
    """Write codefuse token into BaaS bot container."""


@endpoint_test(
    method="PUT",
    path="/api/aicoding/bots/{bot_id}/codefuse/auth",
    scenario="err_bot_not_found",
    seed=_seed_owner_only,
    input=CaseInput(
        path_params={"bot_id": "nonexistent_bot"},
        json_body={"token": _VALID_AUTH_CODE},
        headers={"x-user-id": _OWNER},
    ),
    expect=ExpectError(status=404),
)
def test_codefuse_token_not_found():
    """Bot not found returns 404."""


@endpoint_test(
    method="PUT",
    path="/api/aicoding/bots/{bot_id}/codefuse/auth",
    scenario="err_invalid_auth_code",
    seed=_seed_owner_with_bot_and_baas_binding,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        json_body={"token": "not-a-valid-base64-auth-code"},
        headers={"x-user-id": _OWNER},
    ),
    expect=ExpectError(status=400),
)
def test_codefuse_token_invalid_auth_code():
    """Invalid (non-base64) auth_code returns 400."""