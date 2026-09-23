"""``LocalProcessManager`` 与注册表的同步登记（生产侧 hook）单测。

URL 与 runtime 生命周期解耦的另一半：谁在 spawn/stop 时维护
``RuntimeEndpointRegistry``。这里用假进程替换真实 spawn，钉住三条：

* ``start()`` 成功后设备地址立即可解析（127.0.0.1 + adapter 端口）；
* ``stop()`` 摘除该设备（binding-routed 调用随之拒绝而不是打旧地址）；
* ``stop_all()`` 清空；重启换端口的场景由 ``start()`` 的后写覆盖语义
  支撑（同 device 再 start 即新地址）。
"""
from __future__ import annotations

from pathlib import Path

from agentclaw.community.plugins.local.process_manager import LocalProcessManager
from agentclaw.community.plugins.local.runtime_endpoints import (
    RuntimeEndpointRegistry,
)


class _FakeProc:
    """替换 spawn 出的真实 subprocess.Popen（含 _kill_process 所需接口）。"""

    pid = 4242

    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return None

    def kill(self):
        self.killed = True

    def terminate(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def _make_manager(monkeypatch, registry: RuntimeEndpointRegistry) -> LocalProcessManager:
    manager = LocalProcessManager()
    manager._endpoint_registry = registry  # 测试注入；生产构造默认走全局 instance
    monkeypatch.setattr(
        LocalProcessManager,
        "_spawn_adapter",
        lambda self, **kwargs: _FakeProc(),
    )
    monkeypatch.setattr(
        LocalProcessManager,
        "_spawn_openclaw",
        lambda self, **kwargs: _FakeProc(),
    )
    return manager


def _seed_dirs(config_dir: Path) -> None:
    """start() Step 1 读取 config_dir/.credentials（正常由 create_sync_sandbox 写入）。"""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".credentials").write_text("{}", encoding="utf-8")


def test_start_registers_device_adapter_endpoint(monkeypatch, tmp_path: Path):
    registry = RuntimeEndpointRegistry()
    manager = _make_manager(monkeypatch, registry)
    _seed_dirs(tmp_path / "conf")

    entry = manager.start(
        device_id="device-a",
        bot_id="bot-1",
        adapter_port=20010,
        engine_port=18800,
        config_dir=tmp_path / "conf",
        workspace_dir=tmp_path / "ws",
        engine="aicoding",  # 跳过 engine spawn，焦点在 adapter 登记
    )

    resolved = registry.resolve("device-a")
    assert resolved is not None
    assert resolved.adapter_url == f"http://127.0.0.1:{entry.adapter_port}"


def test_stop_unregisters_the_device(monkeypatch, tmp_path: Path):
    registry = RuntimeEndpointRegistry()
    manager = _make_manager(monkeypatch, registry)
    _seed_dirs(tmp_path / "conf")
    manager.start(
        device_id="device-a",
        bot_id="bot-1",
        adapter_port=20010,
        engine_port=18800,
        config_dir=tmp_path / "conf",
        workspace_dir=tmp_path / "ws",
        engine="aicoding",
    )

    assert manager.stop("device-a") is True
    assert registry.resolve("device-a") is None


def test_respawn_registers_the_new_port(monkeypatch, tmp_path: Path):
    # gateway.port 变更 → 全进程重启换端口的迁移语义：同 device 再 start
    # 即新地址，无需任何旧值迁移。
    registry = RuntimeEndpointRegistry()
    manager = _make_manager(monkeypatch, registry)
    _seed_dirs(tmp_path / "conf")
    _seed_dirs(tmp_path / "conf2")
    manager.start(
        device_id="device-a",
        bot_id="bot-1",
        adapter_port=20010,
        engine_port=18800,
        config_dir=tmp_path / "conf",
        workspace_dir=tmp_path / "ws",
        engine="aicoding",
    )
    manager.start(
        device_id="device-a",
        bot_id="bot-1",
        adapter_port=20042,
        engine_port=18801,
        config_dir=tmp_path / "conf2",
        workspace_dir=tmp_path / "ws2",
        engine="aicoding",
    )

    assert registry.resolve("device-a").adapter_url == "http://127.0.0.1:20042"


def test_stop_all_clears_the_registry(monkeypatch, tmp_path: Path):
    registry = RuntimeEndpointRegistry()
    manager = _make_manager(monkeypatch, registry)
    for suffix in ("a", "b"):
        _seed_dirs(tmp_path / f"conf-{suffix}")
        manager.start(
            device_id=f"device-{suffix}",
            bot_id=f"bot-{suffix}",
            adapter_port=20011 if suffix == "a" else 20012,
            engine_port=18800,
            config_dir=tmp_path / f"conf-{suffix}",
            workspace_dir=tmp_path / f"ws-{suffix}",
            engine="aicoding",
        )

    manager.stop_all()

    assert registry.resolve("device-a") is None
    assert registry.resolve("device-b") is None