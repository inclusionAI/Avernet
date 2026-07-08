"""BaasDeviceFileSystem / DesktopBaasDeviceFileSystem — transport-based.

8 个 file 方法 (read / write / delete / delete_tree / list_dir / exists) 走
注入的 transport。write_file 走 transport.post_multipart(对应 multipart upload),
其他 6 个走 transport.post(json body)。

transport 替换支持 desktop bot 走 secbaas transparent proxy(自拼 URL),其他
baas bot 走 BaaSService.invoke_http(get_http_info → http_url)。

设计:docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx

from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasTransport

logger = get_logger()


class BaasDeviceFileSystem(DeviceFileSystem):
    """DeviceFileSystem — 业务逻辑层,transport 由 dispatcher 注入。

    ``path_mapper`` translates the caller's logical path to the address the engine
    expects, applied before transport — mirrors :class:`ArcaDeviceFileSystem` and
    :class:`TeclawDeviceFileSystem`. The **dispatcher** always supplies it (built
    where ``core`` helpers are allowed) — a passthrough for the generic flow, the
    ``identity/<file>`` composer for the identity flow — so this plugin stays a
    ``core``-free leaf and never defaults the mapper itself. The identity mapper is
    bot-class-agnostic (personal + service bots).
    """

    def __init__(
        self,
        *,
        transport: BaasTransport,
        conn_info: dict[str, Any],
        path_mapper: Callable[[str], str],
    ):
        self._transport = transport
        self._path_mapper = path_mapper
        # for logging only
        self._bot_uuid: str = conn_info.get("paas_device_id", "")

    async def read_file(
        self, file_path: str, *, enforce_download_limit: bool = False
    ) -> bytes | None:
        # ``enforce_download_limit`` is for whole-file-into-memory impls (Arca); the
        # transport here streams, so it is ignored.
        file_path = self._path_mapper(file_path)
        logger.info(
            "[%s.read_file] bot_uuid=%s file=%s",
            type(self).__name__, self._bot_uuid, file_path,
        )
        try:
            resp = await asyncio.to_thread(
                self._transport.post,
                "/api/file/read",
                json={"file_path": file_path},
            )
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as e:
            # Never swallow — a swallowed 401 (missing x-proxypass-token) used to
            # return empty content that callers treated as "file gone", silently
            # dropping promoted files at draft→verify. Surface every failure (404
            # included) so the caller fails loudly; ``exists`` catches 404 itself.
            logger.error(
                "[%s.read_file] HTTP %d: %s",
                type(self).__name__, e.response.status_code, file_path,
            )
            raise
        except Exception as e:
            logger.error("[%s.read_file] error: %s", type(self).__name__, e)
            raise

    async def write_file(self, file_path: str, content: bytes) -> None:
        """Must raise on failure — caller relies on it as upload-failed signal."""
        file_path = self._path_mapper(file_path)
        logger.info(
            "[%s.write_file] bot_uuid=%s file=%s size=%d",
            type(self).__name__, self._bot_uuid, file_path, len(content),
        )
        try:
            resp = await asyncio.to_thread(
                self._transport.post_multipart,
                "/api/file/upload",
                files={"file": (file_path, content)},
                data={"target_path": file_path},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "[%s.write_file] HTTP %d for %s: %s",
                type(self).__name__, e.response.status_code, file_path, e,
            )
            raise
        except Exception as e:
            logger.error(
                "[%s.write_file] error for %s: %s",
                type(self).__name__, file_path, e,
            )
            raise

    async def delete_tree(self, dir_path: str) -> bool:
        dir_path = self._path_mapper(dir_path)
        logger.info(
            "[%s.delete_tree] bot_uuid=%s dir=%s",
            type(self).__name__, self._bot_uuid, dir_path,
        )
        try:
            resp = await asyncio.to_thread(
                self._transport.post,
                "/api/file/rmtree",
                json={"target_path": dir_path},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("[%s.delete_tree] error: %s", type(self).__name__, e)
            return False

    async def delete_file(self, file_path: str) -> bool:
        file_path = self._path_mapper(file_path)
        logger.info(
            "[%s.delete_file] bot_uuid=%s file=%s",
            type(self).__name__, self._bot_uuid, file_path,
        )
        try:
            resp = await asyncio.to_thread(
                self._transport.post,
                "/api/file/remove",
                json={"target_path": file_path},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("[%s.delete_file] error: %s", type(self).__name__, e)
            return False

    async def list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        dir_path = self._path_mapper(dir_path)
        logger.info(
            "[%s.list_dir] bot_uuid=%s dir=%s recursive=%s",
            type(self).__name__, self._bot_uuid, dir_path, recursive,
        )
        try:
            resp = await asyncio.to_thread(
                self._transport.post,
                "/api/file/list",
                json={"dir_path": dir_path, "recursive": recursive},
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("data", {}).get("files", [])
        except httpx.HTTPStatusError as e:
            # Same rationale as ``read_file``: don't swallow. A swallowed listing
            # error returned None, which the teclaw/promotion gather treats as an
            # empty namespace — shipping an artifact missing files. Surface it.
            logger.error(
                "[%s.list_dir] HTTP %d: %s",
                type(self).__name__, e.response.status_code, dir_path,
            )
            raise
        except Exception as e:
            logger.error("[%s.list_dir] error: %s", type(self).__name__, e)
            raise

    async def exists(self, path: str) -> bool:
        # ``exists`` is a boolean probe, so a genuine 404 is the answer (False), not
        # an error — but auth/server failures must still surface (don't swallow).
        try:
            if await self.read_file(path) is not None:
                return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
        try:
            files = await self.list_dir(path)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            return False
        return files is not None


class DesktopBaasDeviceFileSystem(BaasDeviceFileSystem):
    """desktop 链路 — 业务逻辑 100% 继承父类,dispatcher 注入
    DesktopBaasInvokeTransport(自拼 URL)。

    存在目的:日志 / trace 一眼能识别 desktop 链路。
    """
    pass
