"""Golden-master device-push tests for MCP user-config sync (Task 14b).

Pins the device-sync behavior that ``POST /api/mcp/user/config`` drives
through ``MCPSyncService.sync_mcp_detail_to_all_bots`` — the core
orchestration Task 14b routes through the notify layer and promises to
keep ARCA-equivalent.

MCP delivery now rides the ``DeviceSyncPlugin`` boundary: ``MCPSyncService``
resolves a per-bot plugin via ``DeviceContextResolver.resolve_for_bot`` +
``DeviceSyncDispatcher.dispatch(ctx)`` and calls the (synchronous) MCP methods
on it. Phase 2 Task 6 收口后 ``DeviceSyncPluginSupplier`` 已删,本测试改成
monkey-patch dispatcher.dispatch 让其返回一个 recording plugin。
``MCPCenterPlugin`` stays driven via its MockSeam so ``get_mcp_detail`` returns
the seeded catalog entry.

Coverage of the call chain:
- happy: one bot with the MCP installed → exactly one push.
- happy: device reports the MCP absent (has_mcp=False) → push skipped.
- happy: two bots under the entity → the loop pushes to both.
- error: invalid endpoint_env → 400 before any sync.
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test
from tests.community.framework.device_seams import (
    install_fake_resolver,
    install_fake_sync_dispatcher,
)


_OWNER = "u_owner"
_SERVER_CODE = "test.mcp.echo"
_API_KEY = "secret-api-key-123"


class _RecordingPlugin:
    """A per-bot DeviceSyncPlugin double recording MCP calls (methods are sync)."""

    def __init__(self, has_mcp: bool) -> None:
        self._has_mcp = has_mcp
        self.has_mcp_calls: list[str] = []
        self.single_pushes: list[dict] = []
        self.dispatch_calls: list = []

    def has_mcp(self, server_code: str) -> bool:
        self.has_mcp_calls.append(server_code)
        return self._has_mcp

    def sync_single_mcp(self, mcp_data, **kwargs) -> bool:
        self.single_pushes.append(mcp_data)
        return True


# Sequential endpoint tests — stash the active recording plugin for the assertions.
_ACTIVE: dict[str, _RecordingPlugin] = {}


def _insert_bot(world, bot_id: str) -> None:
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
        }
    )


def _configure_seams(world, *, has_mcp: bool = True) -> _RecordingPlugin:
    """Catalog via MockSeam; patch the dispatcher to hand back recording plugin.

    Since ``DeviceSyncDispatcher`` is a singleton in the test world, the
    monkey-patched ``dispatch`` is observed by the same instance MCPSyncService
    holds via its lazy thunk.
    """
    world.get(MCPCenterPlugin).set_override(
        "get_mcp_detail",
        lambda server_code: (
            {"server_code": server_code, "name": "Echo MCP"}
            if server_code == _SERVER_CODE
            else None
        ),
    )
    plugin = _RecordingPlugin(has_mcp)
    install_fake_sync_dispatcher(world, plugin=plugin)

    # Also patch the resolver to return a fake DeviceContext for any bot —
    # the test bot is inserted via the repo without a real binding, so the
    # real ``resolve_for_bot`` would raise DeviceNotBoundError and skip
    # the push entirely.
    install_fake_resolver(world)
    _ACTIVE["plugin"] = plugin
    return plugin


# ---- happy: one bot with the MCP installed → exactly one push ----
def _seed_one_bot(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    _insert_bot(world, "bot_mcp_a")
    _configure_seams(world, has_mcp=True)


def _assert_pushed_single_mcp(response, world) -> None:
    assert response.json().get("success") is True, response.json()
    plugin = _ACTIVE["plugin"]
    assert len(plugin.single_pushes) == 1, plugin.single_pushes
    mcp_data = plugin.single_pushes[0]
    code = mcp_data.get("serverCode") or mcp_data.get("server_code")
    assert code == _SERVER_CODE, mcp_data


@endpoint_test(
    method="POST",
    path="/api/mcp/user/config",
    scenario="happy_one_bot_pushes_single_mcp",
    input=CaseInput(
        query_params={"bot_id": "bot_mcp_a", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
        json_body={"server_code": _SERVER_CODE, "api_key": _API_KEY},
    ),
    seed=_seed_one_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_pushed_single_mcp,),
)
def mcp_config_one_bot_pushes():
    """One reachable bot with the MCP installed → exactly one push."""


# ---- happy: device reports MCP absent (has_mcp=False) → push skipped ----
def _seed_one_bot_without_mcp(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    _insert_bot(world, "bot_mcp_b")
    _configure_seams(world, has_mcp=False)


def _assert_probe_skipped_push(response, world) -> None:
    assert response.json().get("success") is True, response.json()
    plugin = _ACTIVE["plugin"]
    assert len(plugin.has_mcp_calls) == 1, plugin.has_mcp_calls
    assert plugin.single_pushes == [], plugin.single_pushes


@endpoint_test(
    method="POST",
    path="/api/mcp/user/config",
    scenario="happy_device_without_mcp_skips_push",
    input=CaseInput(
        query_params={"bot_id": "bot_mcp_b", "entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
        json_body={"server_code": _SERVER_CODE, "api_key": _API_KEY},
    ),
    seed=_seed_one_bot_without_mcp,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_probe_skipped_push,),
)
def mcp_config_device_without_mcp_skips():
    """Device reports the MCP absent → probe skips the push, still 200."""


# ---- happy: two bots under the entity → loop pushes to both ----
def _seed_two_bots(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    _insert_bot(world, "bot_mcp_c1")
    _insert_bot(world, "bot_mcp_c2")
    _configure_seams(world, has_mcp=True)


def _assert_pushed_to_both_bots(response, world) -> None:
    assert response.json().get("success") is True, response.json()
    plugin = _ACTIVE["plugin"]
    assert len(plugin.single_pushes) == 2, plugin.single_pushes


@endpoint_test(
    method="POST",
    path="/api/mcp/user/config",
    scenario="happy_two_bots_push_each",
    input=CaseInput(
        query_params={"entity_id": _OWNER},
        headers={"x-user-id": _OWNER},
        json_body={"server_code": _SERVER_CODE, "api_key": _API_KEY},
    ),
    seed=_seed_two_bots,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_pushed_to_both_bots,),
)
def mcp_config_two_bots_push_each():
    """Two reachable bots under the entity → the loop pushes to both."""


# ---- error: invalid endpoint_env → 400 before any sync ----
@endpoint_test(
    method="POST",
    path="/api/mcp/user/config",
    scenario="error_invalid_endpoint_env",
    input=CaseInput(
        headers={"x-user-id": _OWNER},
        json_body={
            "server_code": _SERVER_CODE,
            "api_key": _API_KEY,
            "endpoint_env": "INVALID",
        },
    ),
    expect=ExpectError(status=400),
)
def mcp_config_invalid_endpoint_env_400():
    """An invalid endpoint_env is rejected with 400 before any sync."""
