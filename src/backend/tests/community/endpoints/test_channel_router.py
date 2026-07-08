"""Tests for channel endpoints.

Tests the following endpoints from ``adapters/http/channel/router.py``:
- GET /api/channels/openclaw-configs
"""
from __future__ import annotations

from unittest.mock import patch, AsyncMock
import json

from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Test Setup: seed users and bots
# ============================================================================


def _seed_owner_with_bot(world):
    """Seed a bot owner with a bot."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


def _seed_other_user(world):
    """Seed another user (non-owner)."""
    make_staff_user(world, user_id="u_other")


def _seed_owner_and_other_user(world):
    """Seed both owner and other user."""
    _seed_owner_with_bot(world)
    _seed_other_user(world)


# ============================================================================
# Extra Assertions
# ============================================================================


def _assert_openclaw_configs_response_valid(response, world):
    """Assert that response has valid structure for openclaw configs."""
    data = response.json()
    assert "verify" in data, f"Expected 'verify' in response, got keys: {list(data.keys())}"
    assert "online" in data, f"Expected 'online' in response, got keys: {list(data.keys())}"
    assert "eval" in data, f"Expected 'eval' in response, got keys: {list(data.keys())}"
    assert "success" in data, f"Expected 'success' in response, got keys: {list(data.keys())}"
    assert data["success"] is True, f"Expected success=True, got {data['success']}"

    # Verify JSON is valid
    verify_config = json.loads(data["verify"])
    online_config = json.loads(data["online"])
    eval_config = json.loads(data["eval"])
    assert isinstance(verify_config, dict), "verify should be a JSON object"
    assert isinstance(online_config, dict), "online should be a JSON object"
    assert isinstance(eval_config, dict), "eval should be a JSON object"

    # 验证 eval 配置中钉钉渠道已禁用
    if "channels" in eval_config and "dingtalk" in eval_config["channels"]:
        assert eval_config["channels"]["dingtalk"]["enabled"] is False, \
            "dingtalk channel should be disabled in eval config"


def _assert_forbidden_response(response, world):
    """Assert that response is 403 Forbidden."""
    assert response.status_code == 403, f"Expected 403, got {response.status_code}"


# ============================================================================
# GET /api/channels/openclaw-configs
# ============================================================================


def _seed_mock_generate_configs(world):
    """Seed: mock generate_openclaw_configs to return test data."""
    from agentclaw.community.core.channel.services.channel_service import OpenClawConfigs, ChannelService

    async def mock_generate_openclaw_configs(self, *, bot_id, owner_id):
        return OpenClawConfigs(
            verify=json.dumps({"name": "test_verify", "channels": {}}, indent=2),
            online=json.dumps({"name": "test_online", "channels": {}}, indent=2),
            eval=json.dumps({"name": "test_eval", "channels": {"dingtalk": {"enabled": False}}}, indent=2),
        )

    patcher = patch.object(
        ChannelService,
        "generate_openclaw_configs",
        mock_generate_openclaw_configs,
    )
    patcher.start()
    world._channel_generate_configs_patcher = patcher


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="owner_can_access",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=lambda w: (_seed_owner_with_bot(w), _seed_mock_generate_configs(w)),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_openclaw_configs_response_valid,
    ),
)
def get_openclaw_configs_owner_can_access():
    """Owner can access their own openclaw configs."""


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="non_owner_forbidden",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_other"},  # Different user
    ),
    seed=_seed_owner_and_other_user,
    expect=ExpectError(
        status=403,
    ),
)
def get_openclaw_configs_non_owner_forbidden():
    """Non-owner cannot access another user's openclaw configs."""


def _seed_mock_file_not_found(world):
    """Seed: mock generate_openclaw_configs to raise FileNotFoundError."""
    from agentclaw.community.core.channel.services.channel_service import ChannelService

    async def mock_generate_openclaw_configs(self, *, bot_id, owner_id):
        raise FileNotFoundError("openclaw.json not found")

    patcher = patch.object(
        ChannelService,
        "generate_openclaw_configs",
        mock_generate_openclaw_configs,
    )
    patcher.start()
    world._channel_file_not_found_patcher = patcher


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="file_not_found_error",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=lambda w: (_seed_owner_with_bot(w), _seed_mock_file_not_found(w)),
    expect=ExpectError(
        status=500,
    ),
)
def get_openclaw_configs_file_not_found_error():
    """FileNotFoundError returns 500 status."""


def _seed_mock_bot_not_found(world):
    """Seed: mock generate_openclaw_configs to raise BotNotFoundError."""
    from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
    from agentclaw.community.core.channel.services.channel_service import ChannelService

    async def mock_generate_openclaw_configs(self, *, bot_id, owner_id):
        raise BotNotFoundError(f"Bot not found: {bot_id}")

    patcher = patch.object(
        ChannelService,
        "generate_openclaw_configs",
        mock_generate_openclaw_configs,
    )
    patcher.start()
    world._channel_bot_not_found_patcher = patcher


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="bot_not_found_error",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=lambda w: (_seed_owner_with_bot(w), _seed_mock_bot_not_found(w)),
    expect=ExpectError(
        status=400,
    ),
)
def get_openclaw_configs_bot_not_found_error():
    """BotNotFoundError returns 400 status."""


def _seed_mock_unexpected_error(world):
    """Seed: mock generate_openclaw_configs to raise unexpected error."""
    from agentclaw.community.core.channel.services.channel_service import ChannelService

    async def mock_generate_openclaw_configs(self, *, bot_id, owner_id):
        raise RuntimeError("Unexpected error")

    patcher = patch.object(
        ChannelService,
        "generate_openclaw_configs",
        mock_generate_openclaw_configs,
    )
    patcher.start()
    world._channel_unexpected_error_patcher = patcher


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="unexpected_error",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=lambda w: (_seed_owner_with_bot(w), _seed_mock_unexpected_error(w)),
    expect=ExpectError(
        status=500,
    ),
)
def get_openclaw_configs_unexpected_error():
    """Unexpected errors return 500 status."""