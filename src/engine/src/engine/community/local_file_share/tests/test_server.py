from __future__ import annotations

import asyncio
import json
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from engine.community.core.chat_file_share.models import (
    ChatFileShareError,
    ChatFileShareResult,
)
from engine.community.local_file_share.server import LocalFileShareServer


class _Service:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def share(
        self,
        *,
        relative_path: str,
        session_key: str,
    ) -> ChatFileShareResult:
        self.requests.append((relative_path, session_key))
        return ChatFileShareResult(
            file_name="report.txt",
            size_bytes=7,
            share_url="https://oss.example/report.txt?signature=redacted",
            expires_at="2099-08-23T00:00:00Z",
        )


class _FailingService:
    async def share(
        self,
        *,
        relative_path: str,
        session_key: str,
    ) -> ChatFileShareResult:
        raise ChatFileShareError("file_share_timeout")


class _BrokenService:
    async def share(
        self,
        *,
        relative_path: str,
        session_key: str,
    ) -> ChatFileShareResult:
        raise RuntimeError("unexpected")


async def _request(socket_path: Path, payload: object) -> dict[str, object]:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()
    response = json.loads((await reader.readline()).decode("utf-8"))
    writer.close()
    await writer.wait_closed()
    return response


@pytest.fixture
def socket_dir() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="tcfs-", dir="/tmp"))
    directory.chmod(0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.asyncio
async def test_uds_server_forwards_the_inherited_chat_session_only(
    socket_dir: Path,
) -> None:
    socket_path = socket_dir / "file-share.sock"
    service = _Service()
    server = LocalFileShareServer(socket_path=socket_path, service=service)

    await server.start()
    try:
        socket_mode = stat.S_IMODE(socket_path.stat().st_mode)
        response = await _request(
            socket_path,
            {
                "method": "share",
                "relative_path": "report.txt",
                "session_key": "current-chat-session",
            },
        )
    finally:
        await server.close()

    assert socket_mode == 0o600
    assert response == {
        "ok": True,
        "data": {
            "file_name": "report.txt",
            "size_bytes": 7,
            "share_url": "https://oss.example/report.txt?signature=redacted",
            "expires_at": "2099-08-23T00:00:00Z",
        },
    }
    assert service.requests == [("report.txt", "current-chat-session")]


@pytest.mark.asyncio
async def test_uds_server_rejects_missing_or_caller_controlled_fields(
    socket_dir: Path,
) -> None:
    socket_path = socket_dir / "file-share.sock"
    service = _Service()
    server = LocalFileShareServer(socket_path=socket_path, service=service)

    await server.start()
    try:
        missing_session = await _request(
            socket_path,
            {"method": "share", "relative_path": "report.txt"},
        )
        unexpected_identity = await _request(
            socket_path,
            {
                "method": "share",
                "relative_path": "report.txt",
                "session_key": "current-chat-session",
                "tenant": "caller-controlled",
            },
        )
    finally:
        await server.close()

    assert missing_session == {"ok": False, "error": {"code": "invalid_request"}}
    assert unexpected_identity == {
        "ok": False,
        "error": {"code": "invalid_request"},
    }
    assert service.requests == []


@pytest.mark.asyncio
async def test_uds_server_preserves_service_errors_and_hides_unexpected_errors(
    socket_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    socket_path = socket_dir / "file-share.sock"
    server = LocalFileShareServer(socket_path=socket_path, service=_FailingService())

    await server.start()
    try:
        expected_error = await _request(
            socket_path,
            {
                "method": "share",
                "relative_path": "report.txt",
                "session_key": "current-chat-session",
            },
        )
    finally:
        await server.close()

    broken_socket_path = socket_dir / "file-share-broken.sock"
    broken_server = LocalFileShareServer(
        socket_path=broken_socket_path,
        service=_BrokenService(),
    )
    await broken_server.start()
    try:
        with caplog.at_level("WARNING", logger="engine.chat_file_share"):
            unexpected_error = await _request(
                broken_socket_path,
                {
                    "method": "share",
                    "relative_path": "report.txt",
                    "session_key": "current-chat-session",
                },
            )
    finally:
        await broken_server.close()

    assert expected_error == {"ok": False, "error": {"code": "file_share_timeout"}}
    assert unexpected_error == {"ok": False, "error": {"code": "file_share_failed"}}
    assert "unexpected" not in caplog.text


@pytest.mark.asyncio
async def test_uds_server_creates_a_private_missing_socket_parent(
    socket_dir: Path,
) -> None:
    service = _Service()
    socket_path = socket_dir / "missing" / "file-share.sock"
    server = LocalFileShareServer(
        socket_path=socket_path,
        service=service,
    )

    await server.start()
    try:
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_uds_server_rejects_relative_or_non_private_socket_parent(
    socket_dir: Path,
) -> None:
    service = _Service()
    relative = LocalFileShareServer(
        socket_path=Path("relative-file-share.sock"),
        service=service,
    )
    with pytest.raises(ValueError, match="socket parent is unavailable"):
        await relative.start()

    non_private_parent = socket_dir / "non-private"
    non_private_parent.mkdir()
    non_private_parent.chmod(0o755)
    non_private = LocalFileShareServer(
        socket_path=non_private_parent / "file-share.sock",
        service=service,
    )
    with pytest.raises(ValueError, match="socket parent must be private"):
        await non_private.start()

    occupied_path = socket_dir / "not-a-socket"
    occupied_path.write_text("not a socket", encoding="utf-8")
    occupied = LocalFileShareServer(socket_path=occupied_path, service=service)
    with pytest.raises(ValueError, match="socket path is not a socket"):
        await occupied.start()


@pytest.mark.asyncio
async def test_uds_server_never_replaces_an_active_socket(socket_dir: Path) -> None:
    socket_path = socket_dir / "file-share.sock"
    first_service = _Service()
    first_server = LocalFileShareServer(
        socket_path=socket_path,
        service=first_service,
    )
    second_server = LocalFileShareServer(socket_path=socket_path, service=_Service())

    await first_server.start()
    try:
        with pytest.raises(ValueError, match="socket is already active"):
            await second_server.start()
        response = await _request(
            socket_path,
            {
                "method": "share",
                "relative_path": "report.txt",
                "session_key": "current-chat-session",
            },
        )
    finally:
        await first_server.close()

    assert response["ok"] is True
    assert first_service.requests == [("report.txt", "current-chat-session")]
