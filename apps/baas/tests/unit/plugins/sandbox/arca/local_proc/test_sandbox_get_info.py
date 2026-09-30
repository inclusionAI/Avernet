"""Coverage tests for LocalProcessArcaSandbox.get_info().

Verifies that the local-process sandbox returns the unified ArcaSandboxInfo model
while preserving the backend-specific extra fields, covering both
process-present and process-absent branches for adapter and engine processes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from secbaas.community.plugins.sandbox.arca.local_proc._process_manager import (
    ProcessEntry,
)
from secbaas.community.plugins.sandbox.arca.local_proc._sandbox import (
    LocalProcessArcaSandbox,
    LocalProcessSandboxInfo,
)
from secbaas.community.spi.sandbox.arca import ArcaSandboxInfo


def _fake_process() -> MagicMock:
    proc = MagicMock()
    proc.pid = 12345
    return proc


def _entry(
    *,
    adapter_process=None,
    openclaw_process=None,
    hermes_process=None,
    openclaw_port: int = 0,
    hermes_port: int = 0,
) -> ProcessEntry:
    return ProcessEntry(
        sandbox_id="sb-local-1",
        device_id="dev-1",
        bot_id="bot-1",
        adapter_process=adapter_process,
        adapter_port=9001,
        openclaw_process=openclaw_process,
        openclaw_port=openclaw_port,
        hermes_process=hermes_process,
        hermes_port=hermes_port,
        config_dir=Path("/tmp/sb-local-1/config"),
        workspace_dir=Path("/tmp/sb-local-1/workspace"),
    )


def _sandbox(entry: ProcessEntry) -> LocalProcessArcaSandbox:
    return LocalProcessArcaSandbox(
        sandbox_id="sb-local-1", template_id="openclaw", process_entry=entry
    )


class TestLocalProcessSandboxGetInfo:
    def test_returns_unified_sandbox_info_with_extras(self) -> None:
        info = _sandbox(_entry(adapter_process=_fake_process())).get_info()

        assert isinstance(info, ArcaSandboxInfo)
        assert isinstance(info, LocalProcessSandboxInfo)
        assert info.sandbox_id == "sb-local-1"
        assert info.status == "RUNNING"
        assert info.template_id == "openclaw"
        assert info.is_ready is True
        assert info.bot_id == "bot-1"
        assert info.adapter_port == 9001
        assert info.adapter_pid == 12345
        assert info.engine_port == 0
        assert info.engine_pid is None
        assert info.config_dir == "/tmp/sb-local-1/config"
        assert info.workspace_dir == "/tmp/sb-local-1/workspace"
        assert info.resources is None
        assert info.envs is None
        assert info.snapshot_id is None
        assert info.metadata is None
        assert info.outbound_operation_rule is None
        assert info.storage is None
        assert info.ttl_in_minutes is None
        assert info.ttl_timestamp is None

    def test_engine_port_from_openclaw(self) -> None:
        entry = _entry(
            adapter_process=_fake_process(),
            openclaw_process=_fake_process(),
            openclaw_port=9200,
        )
        info = _sandbox(entry).get_info()

        assert info.engine_port == 9200
        assert info.engine_pid == 12345

    def test_engine_port_from_hermes(self) -> None:
        entry = _entry(
            adapter_process=_fake_process(),
            hermes_process=_fake_process(),
            hermes_port=9300,
        )
        info = _sandbox(entry).get_info()

        assert info.engine_port == 9300
        assert info.engine_pid == 12345

    def test_missing_adapter_process_pid_none(self) -> None:
        entry = _entry(
            adapter_process=None,
            openclaw_process=None,
            hermes_process=None,
        )
        info = _sandbox(entry).get_info()

        assert info.adapter_pid is None
        assert info.engine_pid is None

    def test_repr_includes_local_fields(self) -> None:
        info = _sandbox(_entry(adapter_process=_fake_process())).get_info()
        text = repr(info)

        assert "LocalProcessSandboxInfo(" in text
        assert "sb-local-1" in text
        assert "9001" in text
