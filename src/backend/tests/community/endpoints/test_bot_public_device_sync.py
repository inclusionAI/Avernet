"""Golden-master device-push tests for bot publicity (Task 14c).

``test_bot_public_router.py`` already pins the permission/404 envelope of
``POST /api/bots/{bot_id}/public``. This file pins the **device
sync_bot_config call** the publish path makes — the behavior Task 14c
relocates into the notify layer and promises to keep ARCA-equivalent.

The device boundary is the **real** local plugin (``LocalDeviceSyncPlugin``
carrying the ``MockSeam``). Phase 2 Task 6 收口后 ``DeviceSyncPluginSupplier``
已删,改注入 ``DeviceContextResolver`` + ``DeviceSyncDispatcher``。本测试通过
monkey-patch dispatcher.dispatch 让其返回一个 recording ``LocalDeviceSyncPlugin``
(skills_dir=None → noop),再读其 ``calls``。
"""
from __future__ import annotations

from agentclaw.community.plugins.local.device_sync import LocalDeviceSyncPlugin
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.factories.devices import make_active_local_device
from tests.community.framework import CaseInput, ExpectSuccess, endpoint_test
from tests.community.framework.device_seams import (
    install_fake_resolver,
    install_fake_sync_dispatcher,
)


_OWNER = "u_owner"
_OWNER_NAME = "Owner U"


def _record_dispatch(world) -> LocalDeviceSyncPlugin:
    """Wire dispatcher + resolver to a recording local plugin via the framework
    seam helper (out of scope for the ``no-mock-on-world-get`` scanner)."""
    rec = LocalDeviceSyncPlugin(skills_dir=None)  # real impl, noop + records
    install_fake_resolver(
        world,
        provider="baas",
        conn_info={"url": "http://test", "binding_id": world._binding_id},
        binding_id=world._binding_id,
    )
    install_fake_sync_dispatcher(world, plugin=rec)
    world._rec_plugin = rec
    return rec


def _seed_owner_bot_with_binding(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    binding_id = make_active_local_device(world, owner_id=_OWNER)
    make_bot(
        world,
        bot_id="bot_pub_sync",
        owner_id=_OWNER,
        owner_name=_OWNER_NAME,
        bot_type="service",
        status="ACTIVE",
        binding_id=binding_id,
    )
    world._binding_id = binding_id
    _record_dispatch(world)


def _assert_public_pushed_bot_config(response, world) -> None:
    assert response.json().get("success") is True, response.json()

    rec = world._rec_plugin
    calls = rec.calls_to("sync_bot_config")
    assert len(calls) == 1, rec.calls
    kw = calls[0].kwargs
    assert kw["bot_id"] == "bot_pub_sync", kw
    assert kw["binding_id"] == world._binding_id, kw
    assert kw["public"] == "1", kw
    assert kw["user_id"] == _OWNER, kw


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="owner_public_pushes_bot_config_to_device",
    input=CaseInput(
        path_params={"bot_id": "bot_pub_sync"},
        headers={"x-user-id": _OWNER},
        json_body={"public": "1", "permission_owner": "caller", "friend_approval": "0"},
    ),
    seed=_seed_owner_bot_with_binding,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_public_pushed_bot_config,),
)
def public_bot_pushes_bot_config():
    """Publishing a bot pushes ROLE/VISIBILITY to the device once."""


# Error path is already covered by test_bot_public_router.py (403/404
# envelopes), so this file only adds the device-push happy case. The
# route's happy+error coverage is satisfied across both files.
