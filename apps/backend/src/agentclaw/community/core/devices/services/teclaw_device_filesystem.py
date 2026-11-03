"""TeclawDeviceFileSystem — teclaw file edits forwarded per-file to the engine.

Like Baas/Arca, every read **and write** is a per-file call to the engine's HTTP
file API; the **engine** owns its NAS/ossfs mount and does the landing. So
``write_file`` → ``/api/v1/file/upload`` (multipart), ``delete_file`` →
``/api/v1/file/remove`` and ``delete_tree`` → the same recursive-capable
``/api/v1/file/remove``, ``read_file`` → ``/api/v1/file/read``, ``list_dir`` →
``/api/v1/file/list``. There is **no** OSS materialize and **no** whole-artifact
redeliver on an edit — the running container is the source of truth for a bot's
files. (Files are carried to a new version only at draft→verify / verify→publish,
by a separate promotion step that gathers from the engine into OSS + composes
refs.)

The engine path is ``path_mapper(file_path)`` — the engine-relative form under one
of two namespaces: ``/workspace/...`` (resources) or ``/identity/...`` (identity).
See ``core/config_compose/teclaw_paths.to_engine_relative``. The engine's
``_convert_path`` maps that onto its mount.

Transport: the container is reached through the **agentclawproxy** gateway
(``http_url`` is ``{base}/proxypass/{target}{path}``, same gateway ARCA uses),
authenticated with ``x-proxypass-token`` — NOT the secbaas invoke-http tunnel's
``openclawToken``. Calls go through the injected ``BaasInvokeTransport`` (the
same cloud transport :class:`BaasDeviceFileSystem` uses), which resolves the
proxypass ``http_url`` + proxy token via ``get_http_info`` (from the teclaw
``conn_info``'s ``bind_id`` + ``engine_port`` + ``tenant``) and caches that
resolution per engine path for the life of this instance. Every ``read_file``
hits the same ``/api/v1/file/read`` path, so a promotion sweep over N files costs
one binding lookup + one BaaS ``/http-info`` round trip, not N. The transport is
synchronous, so calls run on a thread.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx

from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.baas_invoke_transport import (
    BaasTransport,
)
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem

logger = get_logger()


class TeclawDeviceFileSystem(DeviceFileSystem):
    """Forward teclaw file reads/writes per-file to the engine (no OSS/redeliver)."""

    def __init__(
        self,
        *,
        transport: BaasTransport,
        conn_info: dict[str, Any],
        path_mapper: Callable[[str], str],
    ) -> None:
        """
        Args:
            transport: Issues the per-file engine-API calls over the agentclawproxy
                gateway — a ``BaasInvokeTransport`` the resolver builds from this
                ``conn_info``'s ``bind_id`` (``ac_entity_device_binding.id``),
                ``engine_port`` and ``tenant``, exactly like
                :class:`BaasDeviceFileSystem`. It caches the resolved ``http_info``
                per path and refreshes it once on a 401.
            conn_info: Teclaw connection info (``build_baas_conn_info_for_http``
                output); only ``paas_device_id`` is read here, for log lines.
            path_mapper: Maps a device host path (or namespace-relative logical
                path) to the **engine-relative** form under ``/workspace`` or
                ``/identity`` — the path the engine knows the file by. Injected
                (``teclaw_paths.to_engine_relative``) so this plugin stays a
                ``core``-free leaf.
        """
        self._transport = transport
        self._path_mapper = path_mapper
        # for log lines only
        self._bot_uuid: str = conn_info.get("paas_device_id", "")

    async def write_file(self, file_path: str, content: bytes) -> None:
        """Forward the write to the engine's per-file upload API.

        The engine path is ``path_mapper(file_path)`` — the engine-relative
        ``/workspace/...`` or ``/identity/...`` form. Uploaded as multipart
        (``file`` part + authoritative ``target_path`` field), matching the
        ARCA Bolt upload contract. Raises on non-2xx — callers rely on
        ``write_file`` raising as the write-failed signal.
        """
        engine_path = self._path_mapper(file_path)
        logger.info(
            "[TeclawDeviceFileSystem.write_file] bot_uuid=%s file=%s size=%d",
            self._bot_uuid, engine_path, len(content),
        )
        response = await asyncio.to_thread(
            self._transport.post_multipart,
            "/api/v1/file/upload",
            files={"file": (engine_path, content)},
            data={"target_path": engine_path},
        )
        response.raise_for_status()

    async def delete_file(self, file_path: str) -> bool:
        """Forward a single-file delete to the engine (``/api/v1/file/remove``)."""
        engine_path = self._path_mapper(file_path)
        try:
            response = await asyncio.to_thread(
                self._transport.post, "/api/v1/file/remove",
                json={"target_path": engine_path},
            )
            response.raise_for_status()
            logger.info("[TeclawDeviceFileSystem.delete_file] removed %s", engine_path)
            return True
        except Exception as e:
            logger.error("[TeclawDeviceFileSystem.delete_file] error: %s: %s", engine_path, e)
            return False

    async def delete_tree(self, dir_path: str) -> bool:
        """Ask teclaw to remove the complete directory tree in one operation.

        Teclaw's 404 cannot distinguish an absent target from an unavailable
        route, so only a 2xx response is accepted and no fallback is attempted.
        """
        engine_dir = self._path_mapper(dir_path)
        try:
            response = await asyncio.to_thread(
                self._transport.post, "/api/v1/file/remove",
                json={"target_path": engine_dir},
            )
            response.raise_for_status()
            logger.info("[TeclawDeviceFileSystem.delete_tree] removed tree %s", engine_dir)
            return True
        except Exception as e:
            logger.error(
                "[TeclawDeviceFileSystem.delete_tree] error: %s: %s", engine_dir, e
            )
            return False

    async def read_file(
        self,
        file_path: str,
        *,
        enforce_download_limit: bool = False,
        preserve_read_errors: bool = False,
    ) -> bytes | None:
        # ``enforce_download_limit`` only applies to whole-file-into-memory impls
        # (Arca); the engine read here streams, so it is ignored.
        # Address the engine by the canonical key (same as the artifact ref / OSS
        # key), not the raw host path — that's the path the engine knows the file by.
        engine_path = self._path_mapper(file_path)
        logger.info(
            "[TeclawDeviceFileSystem.read_file] bot_uuid=%s file=%s",
            self._bot_uuid, engine_path,
        )
        try:
            response = await asyncio.to_thread(
                self._transport.post, "/api/v1/file/read",
                json={"file_path": engine_path},
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(
                    "[TeclawDeviceFileSystem.read_file] not found: %s", file_path
                )
            else:
                logger.error(
                    "[TeclawDeviceFileSystem.read_file] HTTP %d: %s",
                    e.response.status_code, file_path,
                )
                if preserve_read_errors:
                    raise
            return None
        except Exception as e:
            logger.error("[TeclawDeviceFileSystem.read_file] error: %s", e)
            if preserve_read_errors:
                raise
            return None

    async def list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        # Address the engine by the canonical key (same as the artifact ref / OSS
        # key), not the raw host path.
        engine_path = self._path_mapper(dir_path)
        logger.info(
            "[TeclawDeviceFileSystem.list_dir] bot_uuid=%s dir=%s recursive=%s",
            self._bot_uuid, engine_path, recursive,
        )
        try:
            response = await asyncio.to_thread(
                self._transport.post,
                "/api/v1/file/list",
                json={"dir_path": engine_path, "recursive": recursive},
            )
            response.raise_for_status()
            result = response.json()
            return result.get("data", {}).get("files", [])
        except Exception as e:
            logger.error("[TeclawDeviceFileSystem.list_dir] error: %s", e)
            return None

    async def exists(self, path: str) -> bool:
        content = await self.read_file(path)
        if content is not None:
            return True
        files = await self.list_dir(path)
        return files is not None


__all__ = ["TeclawDeviceFileSystem"]
