"""Narrow JSON-RPC Unix-socket adapter for a local Chat file-share CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import stat
from pathlib import Path
from typing import Protocol

from engine.community.core.chat_file_share.models import (
    ChatFileShareError,
    ChatFileShareResult,
)

log = logging.getLogger("engine.chat_file_share")
_MAX_REQUEST_BYTES = 16 * 1024


class _ChatFileShareService(Protocol):
    async def share(
        self,
        *,
        relative_path: str,
        session_key: str,
    ) -> ChatFileShareResult: ...


class LocalFileShareServer:
    """Expose the sharing use case only to same-host Unix-socket clients."""

    def __init__(
        self,
        *,
        socket_path: Path,
        service: _ChatFileShareService,
    ) -> None:
        self._socket_path = socket_path
        self._service = service
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._validate_parent()
        self._remove_stale_socket()
        listener = self._bind_private_socket()
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                sock=listener,
                limit=_MAX_REQUEST_BYTES,
            )
        except Exception:
            listener.close()
            self._socket_path.unlink(missing_ok=True)
            raise
        # COSEC: local Chat clients receive access only through this owner-only
        # socket; a public Engine HTTP route would expose the BaaS capability.

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._remove_stale_socket()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        response: dict[str, object]
        try:
            raw = await reader.readline()
            if not raw or len(raw) > _MAX_REQUEST_BYTES:
                raise ValueError("invalid request")
            request = json.loads(raw.decode("utf-8"))
            relative_path, session_key = self._share_request(request)
            result = await self._service.share(
                relative_path=relative_path,
                session_key=session_key,
            )
            response = {"ok": True, "data": self._result_data(result)}
        except ChatFileShareError as exc:
            response = {"ok": False, "error": {"code": exc.code}}
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            response = {"ok": False, "error": {"code": "invalid_request"}}
        except Exception as exc:
            log.warning(
                "engine.chat_file_share.local_adapter_failed error_type=%s",
                type(exc).__name__,
            )
            response = {"ok": False, "error": {"code": "file_share_failed"}}
        encoded_response = json.dumps(response, separators=(",", ":")).encode("utf-8")
        writer.write(encoded_response + b"\n")
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    @staticmethod
    def _share_request(request: object) -> tuple[str, str]:
        if (
            not isinstance(request, dict)
            or set(request) != {"method", "relative_path", "session_key"}
            or request.get("method") != "share"
            or not isinstance(request.get("relative_path"), str)
            or not isinstance(request.get("session_key"), str)
            or not request["session_key"].strip()
        ):
            raise ValueError("invalid request")
        return request["relative_path"], request["session_key"]

    @staticmethod
    def _result_data(result: ChatFileShareResult) -> dict[str, str | int]:
        return {
            "file_name": result.file_name,
            "size_bytes": result.size_bytes,
            "share_url": result.share_url,
            "expires_at": result.expires_at,
        }

    def _validate_parent(self) -> None:
        parent = self._socket_path.parent
        if not self._socket_path.is_absolute():
            raise ValueError("file share socket parent is unavailable")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_stat = parent.stat()
        # COSEC: only the Engine runtime owner may traverse, replace, or
        # connect to the Unix socket directory.
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.geteuid()
            or stat.S_IMODE(parent_stat.st_mode) & 0o077
        ):
            raise ValueError("file share socket parent must be private")

    def _bind_private_socket(self) -> socket.socket:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        current_umask = os.umask(0o177)
        try:
            listener.bind(str(self._socket_path))
        except Exception:
            listener.close()
            raise
        finally:
            os.umask(current_umask)
        return listener

    def _remove_stale_socket(self) -> None:
        try:
            mode = os.lstat(self._socket_path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise ValueError("file share socket path is not a socket")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(self._socket_path))
        except (ConnectionRefusedError, FileNotFoundError):
            # COSEC: an unconnectable socket is stale; only that case may be
            # unlinked, never a live local adapter belonging to another process.
            self._socket_path.unlink(missing_ok=True)
        except socket.timeout as exc:
            raise ValueError("file share socket is already active") from exc
        except OSError as exc:
            raise ValueError("file share socket cannot be probed") from exc
        else:
            raise ValueError("file share socket is already active")
        finally:
            probe.close()
