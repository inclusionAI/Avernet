"""Coverage tests for UDSServer."""

import asyncio
import json
import os
import struct
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.core.service.paas.desktop.worker_router._models import UDSConfig
from secbaas.community.core.service.paas.desktop.worker_router._uds_server import (
    UDSServer,
)


@pytest.fixture
def tmp_socket_dir(tmp_path):
    # Use /tmp for short paths (AF_UNIX has length limit)
    import os

    d = f"/tmp/secbaas_test_{os.getpid()}"
    os.makedirs(d, exist_ok=True)
    yield d
    # Cleanup
    import shutil

    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def config(tmp_socket_dir):
    return UDSConfig(socket_dir=tmp_socket_dir)


@pytest.fixture
def server(config):
    return UDSServer(config=config)


# ==================== __init__ ====================


class TestInit:
    def test_defaults(self):
        s = UDSServer()
        assert s._server is None
        assert s._socket_path is None

    def test_with_config(self, config):
        s = UDSServer(config=config)
        assert s._config is config

    def test_with_connection_manager(self, config):
        cm = MagicMock()
        s = UDSServer(config=config, connection_manager=cm)
        assert s._connection_manager is cm

    def test_socket_path_property(self, server):
        assert server.socket_path is None


# ==================== _is_socket_alive ====================


class TestIsSocketAlive:
    def test_nonexistent_returns_false(self, server):
        assert server._is_socket_alive("/nonexistent/path.sock") is False

    def test_connection_refused_returns_false(self, server, tmp_socket_dir):
        # Create a socket file without a server listening
        sock_path = os.path.join(tmp_socket_dir, "test.sock")
        os.makedirs(tmp_socket_dir, exist_ok=True)
        Path(sock_path).touch()
        assert server._is_socket_alive(sock_path) is False

    def test_unexpected_error_returns_false(self, server):
        with patch("socket.socket", side_effect=RuntimeError("unexpected")):
            assert server._is_socket_alive("/some/path") is False


# ==================== _cleanup_orphan_sockets ====================


class TestCleanupOrphanSockets:
    def test_dir_not_exists(self, server, tmp_socket_dir):
        # Use a non-existent subdirectory
        server._config.socket_dir = os.path.join(tmp_socket_dir, "nonexistent")
        server._cleanup_orphan_sockets()  # Should not raise

    def test_removes_orphan_sockets(self, server, tmp_socket_dir):
        os.makedirs(tmp_socket_dir, exist_ok=True)
        orphan1 = os.path.join(tmp_socket_dir, "worker_123.sock")
        orphan2 = os.path.join(tmp_socket_dir, "worker_456.sock")
        Path(orphan1).touch()
        Path(orphan2).touch()
        server._cleanup_orphan_sockets()
        assert not os.path.exists(orphan1)
        assert not os.path.exists(orphan2)

    def test_keeps_alive_socket(self, server, tmp_socket_dir):
        import socket as sock_mod

        # Use /tmp for short path (AF_UNIX has length limit)
        alive_sock = f"/tmp/test_alive_{os.getpid()}.sock"
        srv = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        srv.bind(alive_sock)
        srv.listen(1)
        # Patch socket_dir to contain a worker socket that points to alive one
        os.makedirs(tmp_socket_dir, exist_ok=True)
        link_path = os.path.join(tmp_socket_dir, "worker_999.sock")
        Path(link_path).touch()
        # Override _is_socket_alive to return True for our link
        with patch.object(server, "_is_socket_alive", return_value=True):
            server._cleanup_orphan_sockets()
            assert os.path.exists(link_path)
        srv.close()
        if os.path.exists(alive_sock):
            os.unlink(alive_sock)

    def test_unlink_failure(self, server, tmp_socket_dir):
        os.makedirs(tmp_socket_dir, exist_ok=True)
        orphan = os.path.join(tmp_socket_dir, "worker_123.sock")
        Path(orphan).touch()
        with patch("pathlib.Path.unlink", side_effect=OSError("perm denied")):
            server._cleanup_orphan_sockets()  # Should not raise


# ==================== start / stop ====================


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_returns_socket_path(self, server):
        path = await server.start()
        assert path is not None
        assert os.path.exists(path)
        await server.stop()
        assert not os.path.exists(path)

    @pytest.mark.asyncio
    async def test_start_sets_socket_path(self, server):
        await server.start()
        assert server.socket_path is not None
        await server.stop()

    @pytest.mark.asyncio
    async def test_start_creates_dir(self, server, tmp_socket_dir):
        server._config.socket_dir = os.path.join(tmp_socket_dir, "subdir")
        await server.start()
        assert os.path.isdir(server._config.socket_dir)
        await server.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self, server):
        await server.stop()  # Should not raise
        assert server._server is None

    @pytest.mark.asyncio
    async def test_stop_removes_socket_file(self, server):
        await server.start()
        path = server.socket_path
        await server.stop()
        assert not os.path.exists(path)
        assert server.socket_path is None

    @pytest.mark.asyncio
    async def test_start_unlink_failure(self, server, tmp_socket_dir):
        with patch("os.unlink", side_effect=OSError("fail")):
            with pytest.raises(OSError):
                await server.start()

    @pytest.mark.asyncio
    async def test_stop_unlink_failure(self, server):
        await server.start()
        with patch("os.path.exists", return_value=True):
            with patch("os.unlink", side_effect=OSError("fail")):
                await server.stop()  # Should not raise


# ==================== _read_framed_data ====================


class TestReadFramedData:
    @pytest.mark.asyncio
    async def test_read_framed(self, server):
        reader = AsyncMock()
        payload = b'{"action": "test"}'
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        result = await server._read_framed_data(reader)
        assert result == payload

    @pytest.mark.asyncio
    async def test_read_framed_incomplete(self, server):
        reader = AsyncMock()
        reader.readexactly = AsyncMock(side_effect=asyncio.IncompleteReadError(b"", 4))
        with pytest.raises(asyncio.IncompleteReadError):
            await server._read_framed_data(reader)


# ==================== _send_framed_response / _send_error_response ====================


class TestSendResponses:
    @pytest.mark.asyncio
    async def test_send_framed_response(self, server):
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        await server._send_framed_response(writer, {"status": "ok"})
        assert writer.write.called
        writer.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_error_response(self, server):
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        await server._send_error_response(writer, "ERR_CODE", "error message")
        assert writer.write.called
        written_data = writer.write.call_args[0][0]
        # Skip 4-byte length prefix
        payload = json.loads(written_data[4:].decode("utf-8"))
        assert payload["status"] == "error"
        assert payload["error"] == "ERR_CODE"
        assert payload["message"] == "error message"


# ==================== _handle_client ====================


class TestHandleClient:
    def _make_writer(self):
        w = MagicMock()
        w.write = MagicMock()
        w.drain = AsyncMock()
        w.close = MagicMock()
        w.wait_closed = AsyncMock()
        w.get_extra_info = MagicMock(return_value=None)
        return w

    @pytest.mark.asyncio
    async def test_no_connection_manager(self, server):
        server._connection_manager = None
        reader = AsyncMock()
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        # _send_error_response is called, writing an error frame
        writer.write.assert_called()
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response["error"] == "SERVER_NOT_READY"

    @pytest.mark.asyncio
    async def test_pid_mismatch(self, server):
        cm = MagicMock()
        server._connection_manager = cm
        request = {"target_worker_pid": 99999, "envelope": {"machine_id": "m1"}}
        payload = json.dumps(request).encode("utf-8")
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        writer.write.assert_called()
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response["error"] == "PID_MISMATCH"

    @pytest.mark.asyncio
    async def test_missing_machine_id(self, server):
        cm = MagicMock()
        server._connection_manager = cm
        request = {"target_worker_pid": os.getpid(), "envelope": {}}
        payload = json.dumps(request).encode("utf-8")
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response["error"] == "MISSING_MACHINE_ID"

    @pytest.mark.asyncio
    async def test_machine_not_connected(self, server):
        cm = MagicMock()
        cm.is_connected.return_value = False
        server._connection_manager = cm
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {"machine_id": "m1", "action": "test"},
        }
        payload = json.dumps(request).encode("utf-8")
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response["error"] == "MACHINE_NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_command_success_with_request_id(self, server):
        cm = MagicMock()
        cm.is_connected.return_value = True
        cm.send_command_with_request_id = AsyncMock(return_value={"result": "ok"})
        server._connection_manager = cm
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {
                "machine_id": "m1",
                "action": "test",
                "params": {"key": "val"},
                "request_id": "req-1",
            },
        }
        payload = json.dumps(request).encode("utf-8")
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        cm.send_command_with_request_id.assert_called_once()
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_command_success_without_request_id(self, server):
        cm = MagicMock()
        cm.is_connected.return_value = True
        cm.send_command = AsyncMock(return_value={"result": "ok"})
        server._connection_manager = cm
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {
                "machine_id": "m1",
                "action": "test",
                "params": {},
            },
        }
        payload = json.dumps(request).encode("utf-8")
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        cm.send_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_non_dict_result(self, server):
        cm = MagicMock()
        cm.is_connected.return_value = True
        cm.send_command = AsyncMock(return_value="string result")
        server._connection_manager = cm
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {"machine_id": "m1", "action": "test", "params": {}},
        }
        payload = json.dumps(request).encode("utf-8")
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response == {"result": "string result"}

    @pytest.mark.asyncio
    async def test_command_failed(self, server):
        cm = MagicMock()
        cm.is_connected.return_value = True
        cm.send_command = AsyncMock(side_effect=Exception("cmd error"))
        server._connection_manager = cm
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {"machine_id": "m1", "action": "test", "params": {}},
        }
        payload = json.dumps(request).encode("utf-8")
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response["error"] == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_invalid_json(self, server):
        cm = MagicMock()
        server._connection_manager = cm
        payload = b"not valid json"
        reader = AsyncMock()
        reader.readexactly = AsyncMock(
            side_effect=[struct.pack(">I", len(payload)), payload]
        )
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response["error"] == "INVALID_JSON"

    @pytest.mark.asyncio
    async def test_incomplete_read(self, server):
        cm = MagicMock()
        server._connection_manager = cm
        reader = AsyncMock()
        reader.readexactly = AsyncMock(side_effect=asyncio.IncompleteReadError(b"", 4))
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        # Should not write any error response, just close
        writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_unexpected_error(self, server):
        cm = MagicMock()
        server._connection_manager = cm
        reader = AsyncMock()
        reader.readexactly = AsyncMock(side_effect=RuntimeError("unexpected"))
        writer = self._make_writer()

        await server._handle_client(reader, writer)
        written = writer.write.call_args[0][0]
        response = json.loads(written[4:].decode("utf-8"))
        assert response["error"] == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_wait_closed_exception(self, server):
        cm = MagicMock()
        server._connection_manager = cm
        reader = AsyncMock()
        reader.readexactly = AsyncMock(side_effect=asyncio.IncompleteReadError(b"", 4))
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock(side_effect=Exception("closed error"))
        writer.get_extra_info = MagicMock(return_value=None)

        # Should not raise even if wait_closed fails
        await server._handle_client(reader, writer)
