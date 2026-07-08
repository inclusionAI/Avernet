"""Tests for UDS Server lifecycle.

Per D-01: Socket path includes PID.
Per D-02: Socket mode 0o600.
Per D-03: Connection test for orphan detection.
Per D-04: Aggressive cleanup at startup.
"""

import asyncio
import json
import os
import socket
import struct
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

from secbaas.core.service.paas.desktop.worker_router._models import UDSConfig
from secbaas.core.service.paas.desktop.worker_router._uds_server import UDSServer


@pytest.fixture
def temp_socket_dir():
    """Create temporary socket directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def server(temp_socket_dir):
    """Create UDS server with temp config."""
    config = UDSConfig(socket_dir=temp_socket_dir, socket_mode=0o600)
    return UDSServer(config=config)


class TestUDSServerLifecycle:
    """Tests for UDS server startup/shutdown and cleanup."""

    @pytest.mark.asyncio
    async def test_start_creates_socket_with_pid_in_path(self, server):
        """D-01: Socket path includes PID."""
        socket_path = await server.start()
        pid = os.getpid()
        assert f"worker_{pid}.sock" in socket_path
        assert os.path.exists(socket_path)
        await server.stop()

    @pytest.mark.asyncio
    async def test_socket_permissions_0o600(self, server, temp_socket_dir):
        """D-02: Socket mode 0o600 (owner read/write only)."""
        socket_path = await server.start()
        mode = os.stat(socket_path).st_mode
        assert mode & 0o777 == 0o600
        await server.stop()

    @pytest.mark.asyncio
    async def test_stop_removes_socket_file(self, server):
        """Stop removes socket file on shutdown."""
        socket_path = await server.start()
        assert os.path.exists(socket_path)
        await server.stop()
        assert not os.path.exists(socket_path)

    def test_is_socket_alive_true_for_live_socket(self, temp_socket_dir):
        """D-03: Connection test returns True for live socket."""
        config = UDSConfig(socket_dir=temp_socket_dir)
        sock_path = f"{temp_socket_dir}/test_alive.sock"
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(sock_path)
        server_sock.listen(1)
        try:
            uds = UDSServer(config=config)
            assert uds._is_socket_alive(sock_path) is True
        finally:
            server_sock.close()

    def test_is_socket_alive_false_for_dead_socket(self, temp_socket_dir):
        """D-03: Connection test returns False for orphan socket."""
        config = UDSConfig(socket_dir=temp_socket_dir)
        sock_path = f"{temp_socket_dir}/test_dead.sock"
        open(sock_path, "w").close()
        uds = UDSServer(config=config)
        assert uds._is_socket_alive(sock_path) is False

    def test_cleanup_orphan_sockets_removes_dead(self, temp_socket_dir):
        """D-04: Aggressive cleanup removes orphan sockets."""
        for i in range(3):
            open(f"{temp_socket_dir}/worker_{i}.sock", "w").close()
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config)
        server._cleanup_orphan_sockets()
        remaining = list(Path(temp_socket_dir).glob("worker_*.sock"))
        assert len(remaining) == 0


class TestUDSServerProperties:
    """Tests for UDSServer properties and edge cases."""

    def test_socket_path_property_returns_none_initially(self):
        """socket_path returns None when server not started."""
        server = UDSServer()
        assert server.socket_path is None

    @pytest.mark.asyncio
    async def test_socket_path_property_returns_path_after_start(self, server):
        """socket_path returns path after server started."""
        path = await server.start()
        assert server.socket_path == path
        assert "worker_" in server.socket_path
        await server.stop()

    def test_default_config_used_when_none_passed(self):
        """Default UDSConfig used when config is None."""
        import os as _os

        server = UDSServer()
        expected_dir = _os.path.join(_os.path.expanduser("~"), "secbaas_workers")
        assert server._config.socket_dir == expected_dir
        assert server._config.socket_mode == 0o600


class TestUDSServerCleanupEdgeCases:
    """Tests for cleanup edge cases."""

    def test_cleanup_orphan_nonexistent_directory(self, temp_socket_dir):
        """_cleanup_orphan_sockets handles nonexistent directory gracefully."""
        config = UDSConfig(socket_dir=f"{temp_socket_dir}/nonexistent")
        server = UDSServer(config=config)
        server._cleanup_orphan_sockets()

    def test_cleanup_keeps_alive_sockets(self, temp_socket_dir):
        """_cleanup_orphan_sockets keeps sockets that accept connections."""
        sock_path = f"{temp_socket_dir}/worker_test_alive.sock"
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(sock_path)
        server_sock.listen(1)
        try:
            dead_path = f"{temp_socket_dir}/worker_test_dead.sock"
            open(dead_path, "w").close()
            config = UDSConfig(socket_dir=temp_socket_dir)
            uds = UDSServer(config=config)
            uds._cleanup_orphan_sockets()
            assert os.path.exists(sock_path)
            assert not os.path.exists(dead_path)
        finally:
            server_sock.close()

    def test_cleanup_unlink_oserror_handled(self, temp_socket_dir):
        """_cleanup_orphan_sockets handles OSError during unlink."""
        sock_path = f"{temp_socket_dir}/worker_oserror.sock"
        with open(sock_path, "w") as f:
            f.write("dead")
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config)
        with patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
            server._cleanup_orphan_sockets()

    def test_is_socket_alive_unexpected_exception(self, temp_socket_dir):
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config)
        sock_path = f"{temp_socket_dir}/test_unexpected.sock"
        with open(sock_path, "w") as f:
            f.write("not-a-socket")
        with patch.object(socket.socket, "connect", side_effect=ValueError("bogus")):
            result = server._is_socket_alive(sock_path)
            assert result is False


class TestUDSServerStartEdgeCases:
    """Tests for start() edge cases."""

    @pytest.mark.asyncio
    async def test_start_mkdir_oserror(self, temp_socket_dir):
        """start() raises OSError when mkdir fails."""
        file_path = Path(f"{temp_socket_dir}/blocked")
        file_path.touch()
        config = UDSConfig(socket_dir=str(file_path))
        server = UDSServer(config=config)
        with pytest.raises(OSError):
            await server.start()

    @pytest.mark.asyncio
    async def test_start_preemptive_unlink_stale_socket(self, server, temp_socket_dir):
        pid = os.getpid()
        stale_path = f"{temp_socket_dir}/worker_{pid}.sock"
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(stale_path)
        server_sock.listen(1)
        Path(temp_socket_dir).mkdir(parents=True, exist_ok=True)
        try:
            path = await server.start()
            assert os.path.exists(path)
            assert path == stale_path
        finally:
            server_sock.close()
            await server.stop()

    @pytest.mark.asyncio
    async def test_start_bind_oserror(self, temp_socket_dir):
        """start() raises OSError when bind fails after preemptive unlink."""
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config)
        socket_dir = Path(temp_socket_dir)
        socket_dir.mkdir(parents=True, exist_ok=True)

        async def mock_start_unix_server(*args, **kwargs):
            raise OSError("Address already in use")

        with patch("asyncio.start_unix_server", side_effect=mock_start_unix_server):
            with pytest.raises(OSError):
                await server.start()

    @pytest.mark.asyncio
    async def test_start_chmod_applies_when_permissions_differ(self, server):
        real_stat = os.stat

        def fake_stat(path, *, follow_symlinks=True):
            result = real_stat(path, follow_symlinks=follow_symlinks)
            wrong_mode = (result.st_mode & ~0o777) | 0o777
            return os.stat_result(
                (
                    wrong_mode,
                    result.st_ino,
                    result.st_dev,
                    result.st_nlink,
                    result.st_uid,
                    result.st_gid,
                    result.st_size,
                    result.st_atime,
                    result.st_mtime,
                    result.st_ctime,
                )
            )

        with patch("os.stat", side_effect=fake_stat):
            path = await server.start()
            real_mode = real_stat(path).st_mode & 0o777
            assert real_mode == 0o600
            await server.stop()


class TestUDSServerStopEdgeCases:
    """Tests for stop() edge cases."""

    @pytest.mark.asyncio
    async def test_stop_with_oserror_in_unlink(self, server):
        """stop() handles OSError during unlink of socket file."""
        path = await server.start()
        with patch("os.unlink", side_effect=OSError("Cannot remove")):
            await server.stop()
        assert server._socket_path is None


class TestUDSServerHandleClient:
    """Tests for _handle_client flow with mocked ConnectionManager."""

    @staticmethod
    def _make_writer():
        """Create a mock StreamWriter with drain and close support."""
        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        return writer

    @staticmethod
    def _make_connection_manager(is_connected=True, command_result=None):
        """Create a mock ConnectionManager."""
        cm = MagicMock()
        cm.is_connected.return_value = is_connected
        if command_result is not None:
            cm.send_command_with_request_id = AsyncMock(return_value=command_result)
            cm.send_command = AsyncMock(return_value=command_result)
        else:
            cm.send_command_with_request_id = AsyncMock()
            cm.send_command = AsyncMock()
        return cm

    @staticmethod
    def _make_framed_request(request_dict):
        """Encode a request dict into a framed byte sequence."""
        request_bytes = json.dumps(request_dict).encode("utf-8")
        length_bytes = struct.pack(">I", len(request_bytes))
        return [length_bytes, request_bytes]

    @staticmethod
    def _decode_written_frame(writer):
        """Reassemble framed bytes written via writer.write and decode JSON.

        Per D-01: success-path frames are raw mng payload (no envelope wrapper).
        Per D-02/D-03: error-path frames are {status:"error", error:<UPPER_SNAKE>, message:<msg>}.
        """
        # Concatenate all calls to writer.write (frame = 4-byte length + JSON)
        chunks = [call.args[0] for call in writer.write.call_args_list]
        assert chunks, "writer.write was never called"
        buf = b"".join(chunks)
        # Strip the 4-byte big-endian length prefix
        length = struct.unpack(">I", buf[:4])[0]
        payload = buf[4 : 4 + length]
        return json.loads(payload.decode("utf-8"))

    @pytest.mark.asyncio
    async def test_handle_client_no_connection_manager(self, temp_socket_dir):
        """D-03: SERVER_NOT_READY (UPPER_SNAKE) when connection_manager is None."""
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=None)
        reader = MagicMock(spec=asyncio.StreamReader)
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.drain.assert_awaited()
        envelope = self._decode_written_frame(writer)
        assert envelope == {
            "status": "error",
            "error": "SERVER_NOT_READY",
            "message": "UDS server not initialized with ConnectionManager",
        }

    @pytest.mark.asyncio
    async def test_handle_client_pid_mismatch(self, temp_socket_dir):
        """D-03: PID_MISMATCH (UPPER_SNAKE) when target_worker_pid != current_pid."""
        cm = self._make_connection_manager()
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {"target_worker_pid": 99999, "envelope": {"machine_id": "m1"}}
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()
        envelope = self._decode_written_frame(writer)
        assert envelope["status"] == "error"
        assert envelope["error"] == "PID_MISMATCH"

    @pytest.mark.asyncio
    async def test_handle_client_missing_machine_id(self, temp_socket_dir):
        """D-03: MISSING_MACHINE_ID (UPPER_SNAKE) when envelope.machine_id absent."""
        cm = self._make_connection_manager()
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {"target_worker_pid": os.getpid(), "envelope": {}}
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()
        envelope = self._decode_written_frame(writer)
        assert envelope["status"] == "error"
        assert envelope["error"] == "MISSING_MACHINE_ID"

    @pytest.mark.asyncio
    async def test_handle_client_machine_not_connected(self, temp_socket_dir):
        """D-03: MACHINE_NOT_CONNECTED (already UPPER_SNAKE pre-D-03)."""
        cm = self._make_connection_manager(is_connected=False)
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {"machine_id": "m_offline", "action": "test", "params": {}},
        }
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()
        envelope = self._decode_written_frame(writer)
        assert envelope["status"] == "error"
        assert envelope["error"] == "MACHINE_NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_handle_client_send_command_with_request_id(self, temp_socket_dir):
        """D-01: success path emits raw mng payload (no {status:ok, data:...} wrapper)."""
        cm = self._make_connection_manager(
            is_connected=True, command_result={"status": "ok"}
        )
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {
                "machine_id": "m1",
                "action": "do_thing",
                "params": {"key": "value"},
                "request_id": "req-123",
            },
        }
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        cm.send_command_with_request_id.assert_called_once_with(
            "m1", {"action": "do_thing", "params": {"key": "value"}}, "req-123"
        )
        writer.close.assert_called_once()
        # D-01: raw pass-through, NOT {"status":"ok","data":{"status":"ok"}}
        payload = self._decode_written_frame(writer)
        assert payload == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_handle_client_send_command_no_request_id(self, temp_socket_dir):
        """D-01: success path emits raw mng payload (no envelope wrapper).

        Covers D-08: fallback to send_command (which auto-generates a request_id)
        when envelope.request_id is missing/empty.
        """
        cm = self._make_connection_manager(
            is_connected=True, command_result={"status": "ok"}
        )
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {"machine_id": "m1", "action": "do_thing", "params": {}},
        }
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        cm.send_command.assert_called_once_with(
            "m1", {"action": "do_thing", "params": {}}
        )
        # D-08: send_command_with_request_id is NOT used in the no-request_id path.
        cm.send_command_with_request_id.assert_not_called()
        writer.close.assert_called_once()
        # D-01: raw pass-through
        payload = self._decode_written_frame(writer)
        assert payload == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_handle_client_passes_envelope_request_id_verbatim_to_dispatch(
        self, temp_socket_dir
    ):
        """D-07: envelope.request_id flows verbatim to send_command_with_request_id.

        Verifies that uds_server uses envelope.request_id as-is (no regeneration)
        when calling ConnectionManager.send_command_with_request_id, completing
        the four-hop trace started by _route_command at the upstream end.
        """
        cm = self._make_connection_manager(
            is_connected=True, command_result={"status": "ok"}
        )
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request_id = "machine-X|deadbeefcafe1234deadbeefcafe1234"
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {
                "machine_id": "machine-X",
                "action": "do_thing",
                "params": {},
                "request_id": request_id,
            },
        }
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        cm.send_command_with_request_id.assert_called_once_with(
            "machine-X", {"action": "do_thing", "params": {}}, request_id
        )
        # D-07/D-08: send_command (auto-generating) is NOT used when envelope.request_id is present.
        cm.send_command.assert_not_called()
        writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_client_send_command_non_dict_result_wrapped(
        self, temp_socket_dir
    ):
        """D-01 / T-35-01-03: defensive non-dict result wrapped as {result: <value>}."""
        cm = self._make_connection_manager(
            is_connected=True, command_result="raw-string-result"
        )
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {
                "machine_id": "m1",
                "action": "do_thing",
                "params": {},
                "request_id": "req-1",
            },
        }
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        payload = self._decode_written_frame(writer)
        assert payload == {"result": "raw-string-result"}

    @pytest.mark.asyncio
    async def test_handle_client_command_failed(self, temp_socket_dir):
        """D-03: COMMAND_FAILED when send_command raises."""
        cm = self._make_connection_manager(is_connected=True)
        cm.send_command_with_request_id = AsyncMock(
            side_effect=RuntimeError("dispatch error")
        )
        cm.send_command = AsyncMock(side_effect=RuntimeError("dispatch error"))
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {
                "machine_id": "m1",
                "action": "do_thing",
                "params": {},
                "request_id": "req-1",
            },
        }
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()
        envelope = self._decode_written_frame(writer)
        assert envelope["status"] == "error"
        assert envelope["error"] == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_handle_client_incomplete_read(self, temp_socket_dir):
        cm = self._make_connection_manager()
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = asyncio.IncompleteReadError(
            partial=b"partial", expected=100
        )
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_client_invalid_json(self, temp_socket_dir):
        """D-03: INVALID_JSON (UPPER_SNAKE) when request frame is not valid JSON."""
        cm = self._make_connection_manager()
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        invalid_bytes = b"not valid json"
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = [
            struct.pack(">I", len(invalid_bytes)),
            invalid_bytes,
        ]
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()
        envelope = self._decode_written_frame(writer)
        assert envelope["status"] == "error"
        assert envelope["error"] == "INVALID_JSON"

    @pytest.mark.asyncio
    async def test_handle_client_unexpected_exception(self, temp_socket_dir):
        """D-03: COMMAND_FAILED (consolidated; was 'internal_error') for unexpected exceptions."""
        cm = self._make_connection_manager(is_connected=True)
        cm.is_connected.side_effect = Exception("Unexpected error")
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {"target_worker_pid": os.getpid(), "envelope": {"machine_id": "m1"}}
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()
        envelope = self._decode_written_frame(writer)
        assert envelope["status"] == "error"
        assert envelope["error"] == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_handle_client_wait_closed_exception_swallowed(self, temp_socket_dir):
        cm = self._make_connection_manager(
            is_connected=True, command_result={"status": "ok"}
        )
        config = UDSConfig(socket_dir=temp_socket_dir)
        server = UDSServer(config=config, connection_manager=cm)
        request = {
            "target_worker_pid": os.getpid(),
            "envelope": {
                "machine_id": "m1",
                "action": "test",
                "params": {},
                "request_id": "req-1",
            },
        }
        fake_frames = self._make_framed_request(request)
        reader = MagicMock(spec=asyncio.StreamReader)
        reader.readexactly.side_effect = fake_frames
        writer = self._make_writer()
        writer.wait_closed = AsyncMock(side_effect=Exception("close error"))
        await server._handle_client(reader, writer)
        writer.close.assert_called_once()
