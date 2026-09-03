"""Tests for channel endpoints.

Tests the following endpoints from ``adapters/http/channel/router.py``:
- GET /api/channels/openclaw-configs

``ChannelService.generate_openclaw_configs`` reads the bot's ``openclaw.json``
off its workspace and merges the channel rows over it, so every case here is
driven by the two things that really decide the outcome: whether the bot
exists, and whether that file is on disk. The happy case writes a real
template at the path the loader resolves for a local device filesystem; the
error cases simply withhold one of the two, which is exactly how each failure
arises in production. The router's catch-all 500 branch is the one thing no
request can reach; that case injects its failure at the DI seam.
"""
from __future__ import annotations

import json

from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.core.channel.json_config_utils import _get_local_base_dir
from agentclaw.community.core.repository.protocols.chat import ChannelRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_failing_method,
    bind_overrides,
    endpoint_test,
)

_TEMPLATE = {
    "name": "test_bot",
    "channels": {
        "dingtalk": {
            "enabled": True,
            "accounts": {},
        }
    },
}


def _template_path():
    """The file the generator actually opens, as production resolves it.

    ``JsonConfigFile.load`` rewrites the bot workspace path to
    ``${HOME}/.openclaw/<name>`` whenever the device filesystem is the local
    one, so that — not the workspace directory — is where a local-mode
    template has to be. Taken from the production helper so the test cannot
    drift from the rule it depends on.
    """
    return _get_local_base_dir() / "openclaw.json"


def _write_openclaw_template(world) -> None:
    """Put a real ``openclaw.json`` where the generator will look for it."""
    path = _template_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_TEMPLATE, indent=2), encoding="utf-8")


# ============================================================================
# Test Setup: seed users and bots
# ============================================================================


def _seed_owner_with_bot(world):
    """Seed a bot owner and a bot on an ACTIVE, non-BaaS-managed local device.

    Two properties of the binding matter to this route. It must be ACTIVE, or
    ``DeviceContextResolver`` refuses to mint a context and the generator never
    reaches a filesystem. And its ``device_props`` must carry no
    ``adapter_port``: that is precisely how the resolver tells a BaaS-managed
    binding from one that is not, and the latter is what selects
    ``LocalDeviceFileSystem``'s pathlib mode — the branch that reads the
    ``openclaw.json`` this test writes to disk.
    """
    make_staff_user(world, user_id="u_owner")
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id="u_owner",
        entity_type="staff",
        device_id="channel_dev_test",
        device_provider="local",
        env=get_current_env(),
        device_props={"openclaw_port": 18789},
        status="ACTIVE",
        apply_reason="channel endpoint test seed",
        applied_by="u_owner",
    )
    make_bot(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        bot_type="service",
        status="ACTIVE",
        binding_id=binding_id,
    )


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


def _seed_bot_with_openclaw_template(world):
    """Seed the bot and the ``openclaw.json`` the generator merges over."""
    _seed_owner_with_bot(world)
    _write_openclaw_template(world)


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="owner_can_access",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_bot_with_openclaw_template,
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


def _seed_bot_without_openclaw_template(world):
    """Seed the bot but leave its workspace empty.

    A bot whose config has never been synced down is the real shape of this
    failure — the generator has no template to merge over and raises
    ``FileNotFoundError``.
    """
    _seed_owner_with_bot(world)
    _template_path().unlink(missing_ok=True)


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="file_not_found_error",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_bot_without_openclaw_template,
    expect=ExpectError(
        status=500,
    ),
)
def get_openclaw_configs_file_not_found_error():
    """FileNotFoundError returns 500 status."""


def _seed_owner_without_bot(world):
    """Seed only the caller, so the bot lookup itself is what fails."""
    make_staff_user(world, user_id="u_owner")


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="bot_not_found_error",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_owner_without_bot,
    expect=ExpectError(
        status=400,
    ),
)
def get_openclaw_configs_bot_not_found_error():
    """BotNotFoundError returns 400 status."""


def _seed_generator_breaks_unexpectedly(world):
    """Seed the bot, then break the generator the way infrastructure would.

    The 500 branch is the router's catch-all for faults no request can
    provoke; the failure is injected at the DI seam so the branch is still
    covered without pretending some input reaches it.
    """
    from agentclaw.community.api.channel_service import ChannelServiceProtocol

    _seed_bot_with_openclaw_template(world)
    bind_failing_method(
        world,
        ChannelServiceProtocol,
        "generate_openclaw_configs",
        RuntimeError("Unexpected error"),
    )


@endpoint_test(
    method="GET",
    path="/api/channels/openclaw-configs",
    scenario="unexpected_error",
    input=CaseInput(
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_generator_breaks_unexpectedly,
    expect=ExpectError(
        status=500,
    ),
)
def get_openclaw_configs_unexpected_error():
    """Unexpected errors return 500 status."""


# ============================================================================
# POST /api/channels/{channel_id}/delete
# ============================================================================


def _seed_bcn_channel_for_delete(world):
    """Seed an owner, a bot, a bcn_gateway channel row, and record the
    service's two delete paths.

    The internal delete endpoint used to call ``service.delete`` (row-only,
    no BCS cleanup), orphaning the BCS binding of a bcn_gateway row. It must
    route through ``remove_channel`` — the same orchestration the public API
    delete uses — so the binding is removed too.
    """
    _seed_owner_with_bot(world)
    world.get(ChannelRepository).insert_channel(
        type="dingding",
        description="bcn channel",
        identity_id="u_owner",
        bind_bot_id="bot_test",
        config={
            "client_id": "client-1",
            "client_secret": "secret-1",
            "robot_code": "robot-1",
            "binding_mode": "bcn_gateway",
            "bcs_binding_id": "bcs-binding-1",
        },
        status="0",
        stage=None,
    )

    async def _remove_channel(self, channel_id: int):
        self._remove_calls.append(channel_id)

    def _delete(self, channel_id: int):
        self._delete_calls.append(channel_id)

    stand_in = bind_overrides(
        world,
        ChannelServiceProtocol,
        {"remove_channel": _remove_channel, "delete": _delete},
    )
    stand_in._remove_calls = []
    stand_in._delete_calls = []


def _assert_delete_routes_through_remove_channel(response, world):
    """The delete must go through remove_channel (BCS cleanup), not bare delete."""
    service = world.get(ChannelServiceProtocol)
    assert service._remove_calls == [1], (
        "internal delete must call remove_channel (best-effort BCS binding "
        f"cleanup), got remove calls: {service._remove_calls}"
    )
    assert service._delete_calls == [], (
        "internal delete must not call the row-only service.delete anymore"
    )


@endpoint_test(
    method="POST",
    path="/api/channels/{channel_id}/delete",
    scenario="owner_deletes_bcn_channel",
    input=CaseInput(
        path_params={"channel_id": 1},
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_bcn_channel_for_delete,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_delete_routes_through_remove_channel,
    ),
)
def delete_channel_routes_through_remove_channel():
    """Owner deleting a channel goes through remove_channel (with BCS cleanup)."""