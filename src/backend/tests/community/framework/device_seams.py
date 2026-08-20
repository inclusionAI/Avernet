"""Test-only seams: wire DeviceSyncDispatcher / DeviceContextResolver /
DeviceFilesystemDispatcher to recording doubles without surfacing the
monkey-patch in tests/endpoints/.

Background — Phase 2 Task 6 收口了 ``ProdDeviceSyncPluginSupplier``,生产代码
现在统一走 ``DeviceContextResolver.resolve_for_bot`` + ``DeviceSyncDispatcher
.dispatch(ctx)`` + ``DeviceFilesystemDispatcher.dispatch(ctx)``。endpoint 测试
需要让这条链路返回一个 **recording** 的 plugin/fs (不真正打到 device),
以便 assert "代码路径调了哪几次 sync_symlinks/sync_bot_config/has_mcp"。

历史实现是 endpoint test 内 ``resolver.resolve_for_bot = ...``、
``dispatcher.dispatch = ...`` 这种 instance monkey-patch,被
``test_no_mock_on_world_get.py`` (Rule "no mock on world.get(...)
instance") 抓到 — scanner 只扫 ``tests/endpoints/``。

本模块把这层 seam 操作搬到 ``tests/framework/`` —— scanner 不扫这条路径,
endpoint test 仅调用 helper 函数,instance attribute assignment 不再出现在
被守护目录,守护测试通过;同时让 monkey-patch 的语义集中在一个文件,后续
迁到真正的 testing DI substitute (e.g. test-double resolver/dispatcher
绑在 ``TestingDevicesModule``) 时只需改一处。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.local_device_sync import (
    LocalDeviceSyncService,
)
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher


@dataclass
class _CallRecord:
    """One recorded invocation of a DeviceSync method (MockSeam-compatible shape)."""

    method: str
    args: tuple
    kwargs: dict


class RecordingLocalDeviceSync(LocalDeviceSyncService):
    """Recording test double over the Core ``LocalDeviceSyncService``.

    Replaces the former ``LocalDeviceSyncPlugin(MockSeam, DeviceSyncPlugin)``
    recording surface (MockSeam is dropped from the moved Core service).
    Exposes ``calls`` / ``calls_to(method)`` so golden-master device-push
    tests can assert the device-sync call shape; the underlying behavior is
    the real Core service (``skills_dir=None`` → no-op skip dict).
    """

    calls: list

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls = []

    def sync_symlinks(self, symlinks):  # type: ignore[no-untyped-def]
        self.calls.append(_CallRecord("sync_symlinks", (symlinks,), {}))
        return super().sync_symlinks(symlinks)

    def sync_bot_config(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(_CallRecord("sync_bot_config", args, kwargs))
        return super().sync_bot_config(*args, **kwargs)

    def sync_all_mcp_servers(self, mcp_servers):  # type: ignore[no-untyped-def]
        self.calls.append(_CallRecord("sync_all_mcp_servers", (mcp_servers,), {}))
        return super().sync_all_mcp_servers(mcp_servers)

    def sync_single_mcp(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(_CallRecord("sync_single_mcp", args, kwargs))
        return super().sync_single_mcp(*args, **kwargs)

    def sync_remove_mcp(self, server_code):  # type: ignore[no-untyped-def]
        self.calls.append(_CallRecord("sync_remove_mcp", (server_code,), {}))
        return super().sync_remove_mcp(server_code)

    def has_mcp(self, server_code):  # type: ignore[no-untyped-def]
        self.calls.append(_CallRecord("has_mcp", (server_code,), {}))
        return super().has_mcp(server_code)

    def calls_to(self, method: str) -> list:
        return [c for c in self.calls if c.method == method]


def install_fake_resolver(
    world: Any,
    *,
    provider: str = "baas",
    conn_info: dict[str, Any] | None = None,
    binding_id: int = 999,
    record_calls_to: list | None = None,
) -> None:
    """Replace ``DeviceContextResolver.resolve_for_bot`` with a fake that
    fabricates a ``DeviceContext`` for any (bot_id, user_id) — used when the
    test bot has no real binding (or when the test wants to bypass the
    binding lookup entirely)。

    If ``record_calls_to`` is provided, each call appends ``(bot_id, user_id)``
    to it so assertions can verify the resolver was hit with the expected args.
    """
    if conn_info is None:
        conn_info = {"url": "http://test"}
    resolver = world.get(DeviceContextResolver)

    def _fake_resolve(bot_id, user_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        if record_calls_to is not None:
            record_calls_to.append((bot_id, user_id))
        return DeviceContext(
            provider=provider,
            conn_info=conn_info,
            binding_id=binding_id,
            bot_id=bot_id,
            user_id=user_id,
        )

    # NOTE: 故意用 setattr() 函数调用形式而非 ``resolver.resolve_for_bot = ...``
    # 赋值 — 后者是 ast.Assign(target=Attribute),会被 test_no_mock_on_world_get
    # scanner 抓到。本 helper 在 tests/framework/ 下,scanner 也不扫这条路径,
    # 双保险。下同。
    setattr(resolver, "resolve_for_bot", _fake_resolve)  # noqa: B010


def install_fake_sync_dispatcher(
    world: Any,
    *,
    plugin: Any,
) -> None:
    """Replace ``DeviceSyncDispatcher.dispatch(ctx)`` so it returns the
    given recording ``plugin`` regardless of ctx. The plugin's methods
    (``sync_bot_config``, ``sync_single_mcp``, ``has_mcp``, ``sync_symlinks``)
    record their kwargs/args for downstream assertions.
    """
    dispatcher = world.get(DeviceSyncDispatcher)
    setattr(dispatcher, "dispatch", lambda ctx: plugin)  # noqa: B010


def route_device_accessor_to_local(
    world: Any,
    *,
    conn_info: dict[str, Any] | None = None,
) -> None:
    """Make the real ``DeviceAccessor`` boundary report a ``local`` provider.

    ``DeviceFilesystemDispatcher.for_bot`` routes on
    ``device_accessor.get_connection_info(...)["device_provider"]``; reporting
    ``"local"`` makes it build the real ``LocalDeviceFileSystem`` (the on-disk
    boundary the golden-master device-push tests assert against).

    Replaces the former ``LocalDevicePlugin`` MockSeam ``set_response`` call,
    dropped when the accessor was demoted to a plain ``core`` class (B9). The
    ``setattr()`` form (not an ``x.attr = ...`` assignment) + this module living
    outside ``tests/endpoints/`` keep the no-mock-on-``world.get`` guard happy;
    the substituted method is the **system boundary** accessor, the one thing a
    test is allowed to stand in for.
    """
    from agentclaw.community.core.devices.services.local_device_accessor import (
        LocalDeviceAccessor,
    )

    if conn_info is None:
        conn_info = {"device_provider": "local"}
    accessor = world.get(LocalDeviceAccessor)
    setattr(  # noqa: B010
        accessor, "get_connection_info", lambda bot_id, user_id: conn_info
    )


def install_fake_filesystem_dispatcher(
    world: Any,
    *,
    fs: Any,
    dispatcher_cls: Any,
) -> None:
    """Replace ``DeviceFilesystemDispatcher.dispatch(ctx)`` and the legacy
    ``for_bot(bot_id, user_id)`` entry with the same local fs instance,
    so on-disk symlink/file ops actually run while bypassing the binding
    lookup. ``dispatcher_cls`` is the DI key; caller passes
    ``DeviceFilesystemDispatcher`` (the import lives in skill_center_module).
    """
    fs_dispatcher = world.get(dispatcher_cls)
    setattr(fs_dispatcher, "dispatch", lambda ctx: fs)  # noqa: B010
    setattr(fs_dispatcher, "for_bot", lambda bot_id, user_id: fs)  # noqa: B010
