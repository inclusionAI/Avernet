"""Unit tests for DefaultDeviceFileSystemResolver — the binding-ctx lookup + local branch.

The routing tests (tests/di/modules/test_filesystem_dispatcher_dispatch.py) exercise
the baas/arca/teclaw construction branches; this covers ``_build_binding_ctx``'s
degrade-to-None edge cases (errors-are-the-result) and the ``local`` branch.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentclaw.community.core.devices.services.device_filesystem_resolver import (
    DefaultDeviceFileSystemResolver,
)


def _resolver(*, bot=None, bot_error=None, binding=None, binding_error=None):
    bot_repo = MagicMock()
    if bot_error:
        bot_repo.get_by_id_and_owner.side_effect = bot_error
    else:
        bot_repo.get_by_id_and_owner.return_value = bot
    binding_repo = MagicMock()
    if binding_error:
        binding_repo.get_by_id.side_effect = binding_error
    else:
        binding_repo.get_by_id.return_value = binding
    return DefaultDeviceFileSystemResolver(
        baas_service=MagicMock(), bot_repo=bot_repo, binding_repo=binding_repo,
        sandbox_client=MagicMock(),
    )


def _binding(props, *, bid=99, device_id="dev-uuid", entity_id="staff_u1"):
    return SimpleNamespace(id=bid, device_id=device_id, entity_id=entity_id, device_props=props)


def test_binding_ctx_bot_lookup_error_degrades_to_none():
    r = _resolver(bot_error=RuntimeError("db down"))
    assert r._build_binding_ctx("bot-1", "u-1") is None


def test_binding_ctx_no_bot_is_none():
    assert _resolver(bot=None)._build_binding_ctx("bot-1", "u-1") is None


def test_binding_ctx_no_binding_id_is_none():
    assert _resolver(bot={"binding_id": None})._build_binding_ctx("bot-1", "u-1") is None


def test_binding_ctx_binding_lookup_error_degrades_to_none():
    r = _resolver(bot={"binding_id": 99}, binding_error=RuntimeError("boom"))
    assert r._build_binding_ctx("bot-1", "u-1") is None


def test_binding_ctx_binding_missing_is_none():
    r = _resolver(bot={"binding_id": 99}, binding=None)
    assert r._build_binding_ctx("bot-1", "u-1") is None


def test_binding_ctx_no_adapter_port_is_none():
    # device_props without adapter_port → not BaaS-managed → pathlib fallback.
    r = _resolver(bot={"binding_id": 99}, binding=_binding({"tenant": "t"}))
    assert r._build_binding_ctx("bot-1", "u-1") is None


def test_binding_ctx_success_builds_context():
    r = _resolver(
        bot={"binding_id": 99},
        binding=_binding({"adapter_port": 20011, "tenant": "team_claw"}),
    )
    ctx = r._build_binding_ctx("bot-1", "u-1")
    assert ctx is not None
    assert ctx.binding_id == 99
    assert ctx.device_id == "dev-uuid"
    assert ctx.entity_id == "staff_u1"
    assert ctx.adapter_port == 20011
    assert ctx.tenant == "team_claw"


def test_binding_ctx_bot_object_attr_access():
    # bot returned as an object (not dict) → getattr(binding_id) path.
    bot_obj = SimpleNamespace(binding_id=99)
    r = _resolver(bot=bot_obj, binding=_binding({"adapter_port": 1, "tenant": ""}))
    ctx = r._build_binding_ctx("bot-1", "u-1")
    assert ctx is not None and ctx.adapter_port == 1


def test_local_branch_pathlib_fallback_when_no_binding():
    """provider='local' + no binding → LocalDeviceFileSystem in pathlib mode."""
    from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem

    r = _resolver(bot=None)  # no bot → binding_ctx None → pathlib
    ctx = SimpleNamespace(provider="local", conn_info={}, bot_id="b", user_id="u")
    fs = r(ctx, lambda p: p)
    assert isinstance(fs, LocalDeviceFileSystem)
    assert fs._is_baas_mode is False


def test_local_branch_baas_mode_when_binding_present():
    from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem

    r = _resolver(
        bot={"binding_id": 99},
        binding=_binding({"adapter_port": 20011, "tenant": "team_claw"}),
    )
    ctx = SimpleNamespace(provider="local", conn_info={}, bot_id="b", user_id="u")
    fs = r(ctx, lambda p: p)
    assert isinstance(fs, LocalDeviceFileSystem)
    assert fs._is_baas_mode is True
    assert fs._binding_ctx.adapter_port == 20011


# ── baas 分支:device_uuid 从 conn_info 透传给 BaasInvokeTransport ─────


def test_baas_branch_passes_device_uuid_from_conn_info_to_transport():
    """provider='baas' + conn_info 含 device_uuid 时,构造 BaasInvokeTransport 透传该值。

    BaasInvokeTransport / BaasDeviceFileSystem 都在 resolver.__call__ 内部 lazy import,
    所以 patch 它们的源模块路径。捕获 transport 构造参数,断言 device_uuid 已透传。
    """
    from agentclaw.community.core.devices.services import baas_invoke_transport as _bt_mod

    captured: dict = {}

    real_init = _bt_mod.BaasInvokeTransport.__init__

    def _spy_init(self, *args, **kwargs):
        captured["kwargs"] = kwargs
        real_init(self, *args, **kwargs)

    with patch.object(_bt_mod.BaasInvokeTransport, "__init__", _spy_init):
        with patch(
            "agentclaw.community.core.devices.services.baas_device_filesystem.BaasDeviceFileSystem"
        ) as mock_fs_cls:
            mock_fs_cls.return_value = MagicMock()
            r = _resolver()
            ctx = SimpleNamespace(
                provider="baas",
                conn_info={
                    "bind_id": 42,
                    "engine_port": 20003,
                    "tenant": "team_claw",
                    "paas_device_id": "BOT-xyz",
                    "device_uuid": "DEV-xyz",
                },
                bot_type="service",
                bot_id="b",
                user_id="u",
            )
            r(ctx, lambda p: p)

    assert captured["kwargs"].get("device_uuid") == "DEV-xyz"
    assert captured["kwargs"].get("bind_id") == 42


def test_baas_branch_default_device_uuid_none_when_conn_info_missing():
    """conn_info 不含 device_uuid 时,BaasInvokeTransport 收到 device_uuid=None。
    """
    from agentclaw.community.core.devices.services import baas_invoke_transport as _bt_mod

    captured: dict = {}

    real_init = _bt_mod.BaasInvokeTransport.__init__

    def _spy_init(self, *args, **kwargs):
        captured["kwargs"] = kwargs
        real_init(self, *args, **kwargs)

    with patch.object(_bt_mod.BaasInvokeTransport, "__init__", _spy_init):
        with patch(
            "agentclaw.community.core.devices.services.baas_device_filesystem.BaasDeviceFileSystem"
        ) as mock_fs_cls:
            mock_fs_cls.return_value = MagicMock()
            r = _resolver()
            ctx = SimpleNamespace(
                provider="baas",
                conn_info={
                    "bind_id": 42,
                    "engine_port": 20003,
                    "tenant": "team_claw",
                    "paas_device_id": "BOT-xyz",
                },
                bot_type="service",
                bot_id="b",
                user_id="u",
            )
            r(ctx, lambda p: p)

    assert captured["kwargs"].get("device_uuid") is None
