"""Unit tests for StubArcaSandbox and StubArcaSandboxPlugin."""

from __future__ import annotations

import re

import pytest

from secbaas.plugins.sandbox.arca._stub import (
    StubArcaSandbox,
    StubArcaSandboxPlugin,
    StubCommandResult,
)

# ---------------------------------------------------------------------------
# StubCommandResult tests
# ---------------------------------------------------------------------------


class TestStubCommandResult:
    """Verify default field values of StubCommandResult."""

    def test_default_exit_code(self) -> None:
        assert StubCommandResult.exit_code == 0

    def test_default_stdout(self) -> None:
        assert StubCommandResult.stdout == "mock-output"

    def test_default_stderr(self) -> None:
        assert StubCommandResult.stderr == ""

    def test_default_elapsed_time(self) -> None:
        assert StubCommandResult.elapsed_time == 0.0


# ---------------------------------------------------------------------------
# StubArcaSandbox tests
# ---------------------------------------------------------------------------


class TestStubArcaSandboxConstruction:
    """Test StubArcaSandbox.__init__ and basic properties."""

    def test_construction_with_defaults(self) -> None:
        device = StubArcaSandbox("sb-1")
        assert device.sandbox_id == "sb-1"
        assert device.is_ready is True

    def test_construction_with_template_id(self) -> None:
        device = StubArcaSandbox("sb-2", template_id="custom-tpl")
        assert device.sandbox_id == "sb-2"
        # The template_id leaks through get_info only — verify via get_info
        info = device.get_info()
        assert info.template_id == "custom-tpl"

    def test_is_ready_always_true(self) -> None:
        device = StubArcaSandbox("any-id")
        assert device.is_ready is True

    def test_sandbox_id_property(self) -> None:
        device = StubArcaSandbox("sandbox-12345")
        assert device.sandbox_id == "sandbox-12345"


class TestStubArcaSandboxGetInfo:
    """Test StubArcaSandbox.get_info()."""

    def test_returns_dict_with_all_keys(self) -> None:
        device = StubArcaSandbox("sb-info", template_id="tpl-info")
        info = device.get_info()
        assert info.sandbox_id == "sb-info"
        assert info.status == "RUNNING"
        assert info.template_id == "tpl-info"

    def test_get_info_works_with_default_template_id(self) -> None:
        device = StubArcaSandbox("sb-default")
        info = device.get_info()
        assert info.template_id == "mock-template"


class TestStubArcaSandboxDestroy:
    """Test StubArcaSandbox.destroy()."""

    def test_destroy_returns_true(self) -> None:
        device = StubArcaSandbox("sb-destroy")
        assert device.destroy() is True


class TestStubArcaSandboxExecCommand:
    """Test StubArcaSandbox.exec_command()."""

    def test_returns_stub_command_result(self) -> None:
        device = StubArcaSandbox("sb-cmd")
        result = device.exec_command("ls -la")
        assert isinstance(result, StubCommandResult)
        assert result.exit_code == 0
        assert result.stdout == "mock-output"

    def test_accepts_custom_timeout_and_envs(self) -> None:
        device = StubArcaSandbox("sb-cmd2")
        result = device.exec_command("echo hello", timeout_in_millis=5000)
        assert isinstance(result, StubCommandResult)

        result2 = device.exec_command("env", envs={"KEY": "VALUE"})
        assert isinstance(result2, StubCommandResult)

    def test_each_call_returns_new_result(self) -> None:
        device = StubArcaSandbox("sb-cmd3")
        r1 = device.exec_command("a")
        r2 = device.exec_command("b")
        # Each call returns a new StubCommandResult instance
        assert r1 is not r2


class TestStubArcaSandboxUpdateOutboundRule:
    """Test StubArcaSandbox.update_outbound_rule()."""

    def test_returns_true(self) -> None:
        device = StubArcaSandbox("sb-rule")
        result = device.update_outbound_rule("allow-all", "whitelist")
        assert result is True


class TestStubArcaSandboxExtendTtl:
    """Test StubArcaSandbox.extend_ttl()."""

    def test_returns_true(self) -> None:
        device = StubArcaSandbox("sb-ttl")
        result = device.extend_ttl(120)
        assert result is True

    def test_accepts_any_integer(self) -> None:
        device = StubArcaSandbox("sb-ttl2")
        assert device.extend_ttl(0) is True
        assert device.extend_ttl(99999) is True


# ---------------------------------------------------------------------------
# StubArcaSandboxPlugin tests
# ---------------------------------------------------------------------------


class TestStubArcaSandboxPluginInit:
    """Test StubArcaSandboxPlugin.__init__."""

    def test_initializes_with_empty_sandboxes(self) -> None:
        plugin = StubArcaSandboxPlugin()
        assert plugin._sandboxes == {}
        # close should be a no-op even when empty
        plugin.close()  # should not raise


class TestStubArcaSandboxPluginCreateSandbox:
    """Test StubArcaSandboxPlugin.create_sync_sandbox()."""

    def test_returns_stub_arca_device(self) -> None:
        plugin = StubArcaSandboxPlugin()
        device = plugin.create_sync_sandbox("tpl-a")
        assert isinstance(device, StubArcaSandbox)

    def test_generates_unique_sandbox_ids(self) -> None:
        plugin = StubArcaSandboxPlugin()
        d1 = plugin.create_sync_sandbox("tpl-x")
        d2 = plugin.create_sync_sandbox("tpl-x")
        assert d1.sandbox_id != d2.sandbox_id
        assert d1.sandbox_id.startswith("stub-arca-")
        assert len(d1.sandbox_id) == 22  # "stub-arca-" + 12 hex chars

    def test_sandbox_id_is_valid_uuid_hex(self) -> None:
        plugin = StubArcaSandboxPlugin()
        device = plugin.create_sync_sandbox("tpl-uuid")
        hex_part = device.sandbox_id[len("stub-arca-") :]
        assert len(hex_part) == 12
        assert re.fullmatch(r"[0-9a-f]+", hex_part)

    def test_stores_device_in_sandboxes(self) -> None:
        plugin = StubArcaSandboxPlugin()
        device = plugin.create_sync_sandbox("tpl-b")
        assert len(plugin._sandboxes) == 1
        assert plugin._sandboxes[device.sandbox_id] is device

    def test_passes_template_id_to_device(self) -> None:
        plugin = StubArcaSandboxPlugin()
        device = plugin.create_sync_sandbox("tpl-custom")
        info = device.get_info()
        assert info.template_id == "tpl-custom"

    def test_extra_params_are_ignored(self) -> None:
        plugin = StubArcaSandboxPlugin()
        device = plugin.create_sync_sandbox(
            "tpl-z",
            ttl_in_minutes=60,
            envs={"FOO": "bar"},
            mount_points=["/data"],
            resource_spec="spec",
            metadata={"k": "v"},
            outbound_operation_rule="rule",
            storage="s3",
            image="custom:v1",
            timeout_in_millis=10000,
            ready_timeout_in_seconds=30,
        )
        # Should still work — stub ignores extra args
        assert isinstance(device, StubArcaSandbox)
        assert device.get_info().template_id == "tpl-z"


class TestStubArcaSandboxPluginConnectSandbox:
    """Test StubArcaSandboxPlugin.connect_sync_sandbox()."""

    def test_connects_existing_sandbox(self) -> None:
        plugin = StubArcaSandboxPlugin()
        created = plugin.create_sync_sandbox("tpl-c")
        sid = created.sandbox_id

        connected = plugin.connect_sync_sandbox(sid)
        assert isinstance(connected, StubArcaSandbox)
        assert connected.sandbox_id == sid
        assert connected is created  # same instance

    def test_connect_missing_sandbox_creates_on_the_fly(self) -> None:
        """connect_sync_sandbox creates a new sandbox when the id is unknown."""
        plugin = StubArcaSandboxPlugin()
        sandbox = plugin.connect_sync_sandbox("nonexistent")
        assert isinstance(sandbox, StubArcaSandbox)
        assert sandbox.sandbox_id == "nonexistent"
        # It should also be stored in _sandboxes
        assert plugin._sandboxes["nonexistent"] is sandbox

    def test_connects_after_multiple_creations(self) -> None:
        plugin = StubArcaSandboxPlugin()
        for _ in range(3):
            plugin.create_sync_sandbox("tpl-multi")

        # Connect the second one
        sid = list(plugin._sandboxes.keys())[1]
        connected = plugin.connect_sync_sandbox(sid)
        assert connected is plugin._sandboxes[sid]


class TestStubArcaSandboxPluginClose:
    """Test StubArcaSandboxPlugin.close()."""

    def test_close_is_noop(self) -> None:
        plugin = StubArcaSandboxPlugin()
        plugin.create_sync_sandbox("tpl-d")
        plugin.close()  # should not raise or affect state
        assert len(plugin._sandboxes) == 1


class TestStubArcaSandboxPluginResolveHttpConnInfo:
    """Test StubArcaSandboxPlugin.resolve_http_connection_info."""

    def test_default_path(self) -> None:
        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_http_connection_info(
            paas_device_id="sb-001", port=20003
        )
        assert result.http_url == "http://localhost:20003/"
        assert result.token == ""
        # Canonical ARCA_ target format: ARCA_{paas_device_id}:{port}
        assert result.target == "ARCA_sb-001:20003"

    def test_custom_path(self) -> None:
        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_http_connection_info(
            paas_device_id="sb-002", port=8080, path="/api/health"
        )
        assert result.http_url == "http://localhost:8080/api/health"
        assert result.token == ""
        assert result.target == "ARCA_sb-002:8080"

    def test_root_path_explicit(self) -> None:
        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_http_connection_info(
            paas_device_id="sb-003", port=9999, path="/"
        )
        assert result.http_url == "http://localhost:9999/"
        assert result.token == ""
        assert result.target == "ARCA_sb-003:9999"

    def test_returns_http_connection_info_type(self) -> None:
        from secbaas.api.bot_runtime import HttpConnectionInfo

        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_http_connection_info("sb-x", 1111)
        assert isinstance(result, HttpConnectionInfo)

    def test_target_with_template_id(self) -> None:
        """template_id produces ARCA_{paas_device_id}@{template_id}:{port} format."""
        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_http_connection_info(
            paas_device_id="sb-004", port=20003, template_id=42
        )
        assert result.target == "ARCA_sb-004@42:20003"

    def test_target_without_template_id(self) -> None:
        """None template_id produces ARCA_{paas_device_id}:{port} format."""
        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_http_connection_info(
            paas_device_id="sb-005", port=20003, template_id=None
        )
        assert result.target == "ARCA_sb-005:20003"


class TestStubArcaSandboxPluginResolveWsConnInfo:
    """Test StubArcaSandboxPlugin.resolve_ws_conn_info."""

    def test_ws_returns_correct_url(self) -> None:
        from secbaas.api.bot_runtime import WsConnectionInfo

        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_ws_conn_info(
            paas_device_id="sb-ws-001", port=20003, path="/ws"
        )
        assert isinstance(result, WsConnectionInfo)
        assert result.ws_url == "ws://localhost:20003/ws"
        assert result.token == ""
        # Canonical ARCA_ target format without template_id
        assert result.target == "ARCA_sb-ws-001:20003"

    def test_ws_target_with_template_id(self) -> None:
        """template_id produces ARCA_{paas_device_id}@{template_id}:{port} format."""
        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_ws_conn_info(
            paas_device_id="sb-ws-002", port=8080, path="/api/ws", template_id=99
        )
        assert result.target == "ARCA_sb-ws-002@99:8080"

    def test_ws_has_expires_at(self) -> None:
        """expires_at is set and in the future."""
        from datetime import UTC, datetime, timedelta

        plugin = StubArcaSandboxPlugin()
        result = plugin.resolve_ws_conn_info("sb-ws-003", 443, "/ws")
        assert isinstance(result.expires_at, datetime)
        now = datetime.now(UTC)
        assert result.expires_at > now
        assert result.expires_at < now + timedelta(hours=25)
