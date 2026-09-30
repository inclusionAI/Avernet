"""Coverage tests for LocalProcessArcaSandboxPlugin."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.plugins.sandbox.arca.local_proc._sandbox_plugin import (
    LocalProcessArcaSandboxPlugin,
)


@pytest.fixture
def plugin():
    with patch(
        "secbaas.community.plugins.sandbox.arca.local_proc._sandbox_plugin.LocalProcessManager"
    ) as mock_mgr_cls:
        mock_mgr = MagicMock()
        mock_mgr_cls.instance.return_value = mock_mgr
        p = LocalProcessArcaSandboxPlugin()
        yield p, mock_mgr


# ==================== create_sync_sandbox ====================


class TestCreateSyncSandbox:
    def test_metadata_none_raises(self, plugin):
        p, mgr = plugin
        with pytest.raises(ValueError, match="metadata should not be None"):
            p.create_sync_sandbox(template_id="openclaw", metadata=None)

    def test_missing_tc_bot_id(self, plugin):
        p, mgr = plugin
        with pytest.raises(ValueError, match="tc_bot_id"):
            p.create_sync_sandbox(
                template_id="openclaw",
                metadata={"device_uuid": "d1", "entity_id": "e1", "entity_type": "bot"},
            )

    def test_missing_device_uuid(self, plugin):
        p, mgr = plugin
        with pytest.raises(ValueError, match="device_uuid"):
            p.create_sync_sandbox(
                template_id="openclaw",
                metadata={"tc_bot_id": "b1", "entity_id": "e1", "entity_type": "bot"},
            )

    def test_missing_entity_id(self, plugin):
        p, mgr = plugin
        with pytest.raises(ValueError, match="entity_id"):
            p.create_sync_sandbox(
                template_id="openclaw",
                metadata={"tc_bot_id": "b1", "device_uuid": "d1", "entity_type": "bot"},
            )

    def test_missing_entity_type(self, plugin):
        p, mgr = plugin
        with pytest.raises(ValueError, match="entity_type"):
            p.create_sync_sandbox(
                template_id="openclaw",
                metadata={"tc_bot_id": "b1", "device_uuid": "d1", "entity_id": "e1"},
            )

    def test_create_openclaw_success(self, plugin):
        p, mgr = plugin
        mgr.allocate_ports.return_value = (20018, 30018)
        config_dir = MagicMock()
        mgr.create_openclaw_config.return_value = config_dir
        entry = MagicMock()
        entry.sandbox_id = "sb-1"
        mgr.start.return_value = entry

        sandbox = p.create_sync_sandbox(
            template_id="openclaw",
            metadata={
                "tc_bot_id": "bot-1",
                "device_uuid": "dev-1",
                "entity_id": "ent-1",
                "entity_type": "bot",
            },
        )
        assert sandbox._sandbox_id == "sb-1"
        assert "sb-1" in p._sandboxes

    def test_create_hermes_success(self, plugin, monkeypatch):
        monkeypatch.setenv("CHAT_ENGINE", "hermes")
        p, mgr = plugin
        mgr.allocate_ports.return_value = (20019, 30019)
        mgr.create_hermes_config.return_value = MagicMock()
        entry = MagicMock()
        entry.sandbox_id = "sb-2"
        mgr.start.return_value = entry

        sandbox = p.create_sync_sandbox(
            template_id="hermes",
            metadata={
                "tc_bot_id": "bot-2",
                "device_uuid": "dev-2",
                "entity_id": "ent-2",
                "entity_type": "bot",
            },
        )
        assert sandbox._sandbox_id == "sb-2"

    def test_create_aicoding_success(self, plugin, monkeypatch):
        monkeypatch.setenv("CHAT_ENGINE", "aicoding")
        p, mgr = plugin
        mgr.allocate_ports.return_value = (20020, 30020)
        entry = MagicMock()
        entry.sandbox_id = "sb-3"
        mgr.start.return_value = entry

        sandbox = p.create_sync_sandbox(
            template_id="aicoding",
            metadata={
                "tc_bot_id": "bot-3",
                "device_uuid": "dev-3",
                "entity_id": "ent-3",
                "entity_type": "bot",
            },
        )
        assert sandbox._sandbox_id == "sb-3"

    def test_create_start_failure_sends_callback(self, plugin, monkeypatch):
        monkeypatch.setenv("CHAT_ENGINE", "openclaw")
        p, mgr = plugin
        mgr.allocate_ports.return_value = (20021, 30021)
        mgr.create_openclaw_config.return_value = MagicMock()
        mgr.start.side_effect = RuntimeError("start failed")

        with patch.object(p, "_send_device_callback") as mock_cb:
            with pytest.raises(RuntimeError):
                p.create_sync_sandbox(
                    template_id="openclaw",
                    metadata={
                        "tc_bot_id": "bot-4",
                        "device_uuid": "dev-4",
                        "entity_id": "ent-4",
                        "entity_type": "bot",
                        "publish_id": "123",
                    },
                )
            mock_cb.assert_called_once_with(
                "dev-4",
                {
                    "tc_bot_id": "bot-4",
                    "device_uuid": "dev-4",
                    "entity_id": "ent-4",
                    "entity_type": "bot",
                    "publish_id": "123",
                },
                result_status="FAILED",
                exit_code=1,
                stderr="start failed",
            )

    def test_create_with_ttl(self, plugin):
        p, mgr = plugin
        mgr.allocate_ports.return_value = (20022, 30022)
        mgr.create_openclaw_config.return_value = MagicMock()
        entry = MagicMock()
        entry.sandbox_id = "sb-5"
        mgr.start.return_value = entry

        sandbox = p.create_sync_sandbox(
            template_id="openclaw",
            ttl_in_minutes=120,
            metadata={
                "tc_bot_id": "bot-5",
                "device_uuid": "dev-5",
                "entity_id": "ent-5",
                "entity_type": "bot",
            },
        )
        assert sandbox._ttl_minutes == 120


# ==================== _send_device_callback ====================


class TestSendDeviceCallback:
    def test_no_publish_id_skips(self, plugin):
        p, mgr = plugin
        # Should not raise, just log warning
        p._send_device_callback(
            "dev-1", {"tenant": "t1"}, result_status="SUCCESS", exit_code=0
        )

    def test_none_metadata_skips(self, plugin):
        p, mgr = plugin
        p._send_device_callback("dev-1", None, result_status="SUCCESS", exit_code=0)

    def test_success_callback(self, plugin, monkeypatch):
        p, mgr = plugin
        monkeypatch.setenv("LOCAL_CALLBACK_URL", "http://localhost:8890")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            p._send_device_callback(
                "dev-1",
                {"publish_id": "123", "tenant": "t1"},
                result_status="SUCCESS",
                exit_code=0,
                stdout="created",
            )

    def test_http_error(self, plugin, monkeypatch):
        p, mgr = plugin
        monkeypatch.setenv("LOCAL_CALLBACK_URL", "http://localhost:8890")

        import urllib.error

        err = urllib.error.HTTPError(
            url="http://localhost:8890/api/v1/publish/device-callback",
            code=500,
            msg="Internal Error",
            hdrs=MagicMock(),
            fp=MagicMock(),
        )
        err.fp = MagicMock()
        err.read = MagicMock(return_value=b"error body")

        with patch("urllib.request.urlopen", side_effect=err):
            p._send_device_callback(
                "dev-1",
                {"publish_id": "123", "tenant": "t1"},
                result_status="FAILED",
                exit_code=1,
                stderr="err",
            )

    def test_http_error_no_fp(self, plugin, monkeypatch):
        p, mgr = plugin
        monkeypatch.setenv("LOCAL_CALLBACK_URL", "http://localhost:8890")

        import urllib.error

        err = urllib.error.HTTPError(
            url="http://localhost:8890/api/v1/publish/device-callback",
            code=500,
            msg="Internal Error",
            hdrs=MagicMock(),
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=err):
            p._send_device_callback(
                "dev-1",
                {"publish_id": "123", "tenant": "t1"},
                result_status="FAILED",
                exit_code=1,
                stderr="err",
            )

    def test_connection_error(self, plugin, monkeypatch):
        p, mgr = plugin
        monkeypatch.setenv("LOCAL_CALLBACK_URL", "http://localhost:8890")

        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            p._send_device_callback(
                "dev-1",
                {"publish_id": "123", "tenant": "t1"},
                result_status="SUCCESS",
                exit_code=0,
            )

    def test_empty_callback_url(self, plugin, monkeypatch):
        p, mgr = plugin
        monkeypatch.setenv("LOCAL_CALLBACK_URL", "")
        p._send_device_callback(
            "dev-1",
            {"publish_id": "123", "tenant": "t1"},
            result_status="SUCCESS",
            exit_code=0,
        )


# ==================== connect_sync_sandbox ====================


class TestConnectSyncSandbox:
    def test_cached_sandbox(self, plugin):
        p, mgr = plugin
        cached = MagicMock()
        cached._status = "ACTIVE"
        p._sandboxes["sb-1"] = cached
        result = p.connect_sync_sandbox("sb-1")
        assert result is cached

    def test_cached_destroyed_raises(self, plugin):
        p, mgr = plugin
        cached = MagicMock()
        cached._status = "DESTROYED"
        p._sandboxes["sb-1"] = cached
        with pytest.raises(RuntimeError, match="destroyed"):
            p.connect_sync_sandbox("sb-1")

    def test_not_found(self, plugin):
        p, mgr = plugin
        mgr.get_entry.return_value = None
        with pytest.raises(RuntimeError, match="not found"):
            p.connect_sync_sandbox("sb-unknown")

    def test_hermes_entry(self, plugin):
        p, mgr = plugin
        entry = MagicMock()
        entry.hermes_port = 30020
        entry.openclaw_port = 0
        mgr.get_entry.return_value = entry
        result = p.connect_sync_sandbox("sb-2")
        assert result._template_id == "hermes"

    def test_openclaw_entry(self, plugin):
        p, mgr = plugin
        entry = MagicMock()
        entry.hermes_port = 0
        entry.openclaw_port = 30020
        mgr.get_entry.return_value = entry
        result = p.connect_sync_sandbox("sb-3")
        assert result._template_id == "openclaw"

    def test_aicoding_entry(self, plugin):
        p, mgr = plugin
        entry = MagicMock()
        entry.hermes_port = 0
        entry.openclaw_port = 0
        mgr.get_entry.return_value = entry
        result = p.connect_sync_sandbox("sb-4")
        assert result._template_id == "aicoding"


# ==================== list_sandboxes ====================


class TestListSandboxes:
    def test_empty(self, plugin):
        p, mgr = plugin
        assert p.list_sandboxes() == []

    def test_with_sandboxes(self, plugin):
        p, mgr = plugin
        s1 = MagicMock()
        s1._status = "ACTIVE"
        s1.is_ready = True
        s1._template_id = "openclaw"
        s2 = MagicMock()
        s2._status = "DESTROYED"
        s2.is_ready = False
        s2._template_id = "hermes"
        p._sandboxes = {"sb-1": s1, "sb-2": s2}
        result = p.list_sandboxes()
        assert len(result) == 2
        ids = {r["sandbox_id"] for r in result}
        assert ids == {"sb-1", "sb-2"}


# ==================== close ====================


class TestClose:
    def test_close_empty(self, plugin):
        p, mgr = plugin
        p.close()
        assert len(p._sandboxes) == 0

    def test_close_with_sandboxes(self, plugin):
        p, mgr = plugin
        s1 = MagicMock()
        s2 = MagicMock()
        p._sandboxes = {"sb-1": s1, "sb-2": s2}
        p.close()
        s1.destroy.assert_called_once()
        s2.destroy.assert_called_once()
        assert len(p._sandboxes) == 0

    def test_close_destroy_error(self, plugin):
        p, mgr = plugin
        s1 = MagicMock()
        s1.destroy.side_effect = Exception("destroy failed")
        p._sandboxes = {"sb-1": s1}
        p.close()  # Should not raise
        assert len(p._sandboxes) == 0


# ==================== resolve_ws_conn_info ====================


class TestResolveWsConnInfo:
    def test_success(self, plugin):
        p, mgr = plugin
        entry = MagicMock()
        entry.adapter_port = 20018
        mgr.get_entry.return_value = entry
        info = p.resolve_ws_conn_info("dev-1", 8080, "/api/openclaw/ws")
        assert "ws://localhost:20018/api/openclaw/ws" in info.ws_url
        assert info.token == "local"

    def test_path_without_slash(self, plugin):
        p, mgr = plugin
        entry = MagicMock()
        entry.adapter_port = 20018
        mgr.get_entry.return_value = entry
        info = p.resolve_ws_conn_info("dev-1", 8080, "api/ws")
        assert info.ws_url == "ws://localhost:20018/api/ws"

    def test_device_not_found(self, plugin):
        p, mgr = plugin
        mgr.get_entry.return_value = None
        with pytest.raises(RuntimeError, match="not found"):
            p.resolve_ws_conn_info("unknown", 8080, "/ws")


# ==================== resolve_http_connection_info ====================


class TestResolveHttpConnInfo:
    def test_success(self, plugin):
        p, mgr = plugin
        entry = MagicMock()
        entry.adapter_port = 20018
        mgr.get_entry.return_value = entry
        info = p.resolve_http_connection_info("dev-1", 8080, "/api/health")
        assert "http://localhost:20018/api/health" in info.http_url
        assert info.token == "local"

    def test_default_path(self, plugin):
        p, mgr = plugin
        entry = MagicMock()
        entry.adapter_port = 20018
        mgr.get_entry.return_value = entry
        info = p.resolve_http_connection_info("dev-1", 8080)
        assert info.http_url == "http://localhost:20018/"

    def test_device_not_found(self, plugin):
        p, mgr = plugin
        mgr.get_entry.return_value = None
        with pytest.raises(RuntimeError, match="not found"):
            p.resolve_http_connection_info("unknown", 8080)


# ==================== delete_storage ====================


class TestDeleteStorage:
    def test_returns_true(self, plugin):
        p, mgr = plugin
        result = p.delete_storage("storage-1", "tenant-1")
        assert result is True
