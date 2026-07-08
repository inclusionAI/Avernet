"""LocalDeviceFileSystem -- dual-mode DeviceFileSystem (plan-02).

Two modes:

1. **BaaS mode** (singlebox production): when ctor receives both
   ``baas_service`` and ``binding_ctx``, every public method calls
   ``baas_service.get_http_info(...)`` to resolve container URL + token,
   then httpx-direct POSTs to the container adapter
   (``/api/file/{read,upload,rmtree,list}``). This is the singlebox
   path: caller is unchanged; only the plugin internals route via BaaS.

2. **Pathlib fallback** (contract tests / standalone usage): when either
   ctor arg is missing, public methods route through private
   ``_pathlib_*`` methods that wrap ``pathlib.Path`` + ``shutil``. This
   preserves the historical ``LocalDeviceFileSystem()`` no-arg shape used
   by ``tests/contracts/test_device_filesystem.py`` (JsonConfigFile
   consumer + tmp_path).

Dispatch rule: ``_is_baas_mode = baas_service is not None and binding_ctx
is not None``. Partial wiring (only one arg) falls back to pathlib so the
DI dispatcher can evolve incrementally without surprising half-baked BaaS
calls (test_ctor_partial_params_falls_back_to_pathlib pins this).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import httpx

from agentclaw.community.core.devices.models import DeviceBindingContext
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem

logger = get_logger()


def _passthrough(path: str) -> str:
    return path


class LocalDeviceFileSystem(DeviceFileSystem):
    """DeviceFileSystem with dual mode: BaaS HTTP for singlebox, pathlib fallback for tests."""

    def __init__(
        self,
        baas_service: BaasService | None = None,
        binding_ctx: DeviceBindingContext | None = None,
        *,
        path_mapper: Callable[[str], str] = _passthrough,
    ) -> None:
        self._baas_service = baas_service
        self._binding_ctx = binding_ctx
        self._is_baas_mode = baas_service is not None and binding_ctx is not None
        # Applied at the public boundary so both modes (baas / pathlib) address the
        # mapped path. Defaults to passthrough for the no-arg contract-test shape.
        self._path_mapper = path_mapper

    # ── BaaS mode helpers ─────────────────────────────────────────────

    async def _baas_request(
        self,
        api_path: str,
        *,
        json_body: dict | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Resolve container URL + token via BaaS get_http_info, then POST.

        Centralizes the get_http_info → httpx.AsyncClient.post pattern so
        every method has identical 1-call-per-op semantics (D3) and identical
        header / URL construction. Returns the raw Response so callers decide
        how to interpret status (404 → None vs raise, etc.).

        Caller is responsible for ``response.raise_for_status()`` and the
        per-method error mapping per spec §4.2.

        Raises:
            BaasServiceError: get_http_info failure transparently propagates.
        """
        info = self._baas_service.get_http_info(
            bind_id=self._binding_ctx.binding_id,
            port=self._binding_ctx.adapter_port,
            path=api_path,
            device_affinity=self._binding_ctx.entity_id,
            tenant=self._binding_ctx.tenant or None,
        )
        # http_url 已含 api_path（get_http_info 传 path=api_path, baas 已拼好），
        # 不能再拼一遍，否则 /api/file/upload/api/file/upload double → 404。
        url = info.http_url
        headers = {"openclawToken": info.token}
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=json_body or {}, headers=headers)

    # ── Public API: dispatches to _baas_* or _pathlib_* per ctor mode ──

    async def read_file(
        self, file_path: str, *, enforce_download_limit: bool = False
    ) -> bytes | None:
        # ``enforce_download_limit`` is for whole-file-into-memory impls (Arca); both
        # local modes here ignore it.
        file_path = self._path_mapper(file_path)
        if self._is_baas_mode:
            return await self._baas_read_file(file_path)
        return await self._pathlib_read_file(file_path)

    async def write_file(self, file_path: str, content: bytes) -> None:
        file_path = self._path_mapper(file_path)
        if self._is_baas_mode:
            return await self._baas_write_file(file_path, content)
        return await self._pathlib_write_file(file_path, content)

    async def delete_tree(self, dir_path: str) -> bool:
        dir_path = self._path_mapper(dir_path)
        if self._is_baas_mode:
            return await self._baas_delete_tree(dir_path)
        return await self._pathlib_delete_tree(dir_path)

    async def list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        dir_path = self._path_mapper(dir_path)
        if self._is_baas_mode:
            return await self._baas_list_dir(dir_path, recursive=recursive)
        return await self._pathlib_list_dir(dir_path, recursive=recursive)

    async def exists(self, path: str) -> bool:
        path = self._path_mapper(path)
        if self._is_baas_mode:
            return await self._baas_exists(path)
        return await self._pathlib_exists(path)

    # ── Pathlib fallback (unchanged historical impl) ──────────────────

    async def _pathlib_read_file(self, file_path: str) -> bytes | None:
        p = Path(file_path)
        logger.info("[LocalDeviceFileSystem.pathlib.read_file] %s (exists=%s, is_file=%s)", file_path, p.exists(), p.is_file())
        if not p.is_file():
            return None
        try:
            data = p.read_bytes()
            logger.info("[LocalDeviceFileSystem.pathlib.read_file] OK, %d bytes", len(data))
            return data
        except OSError as e:
            logger.warning("[LocalDeviceFileSystem.pathlib.read_file] %s: %s", file_path, e)
            return None

    async def _pathlib_write_file(self, file_path: str, content: bytes) -> None:
        logger.info("[LocalDeviceFileSystem.pathlib.write_file] %s (%d bytes)", file_path, len(content))
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    async def _pathlib_delete_tree(self, dir_path: str) -> bool:
        p = Path(dir_path)
        logger.info("[LocalDeviceFileSystem.pathlib.delete_tree] %s (exists=%s)", dir_path, p.exists())
        if not p.exists():
            return True
        try:
            # 名字是 ``delete_tree`` 但调用方 FileService.delete_item 会同时传单
            # 文件 path 和目录 path（前端"删文件" / "删文件夹"复用同一端点 + 同
            # 一 Protocol 方法）。早期只调 shutil.rmtree——在单文件路径上 raise
            # NotADirectoryError，被下面 except OSError 吞掉返 False，HTTP 层最终
            # 返 404 "File not found"。修法：先判 file/symlink vs dir，分别走
            # unlink / rmtree。
            if p.is_file() or p.is_symlink():
                p.unlink()
                logger.info("[LocalDeviceFileSystem.pathlib.delete_tree] OK (unlink file)")
            else:
                shutil.rmtree(p)
                logger.info("[LocalDeviceFileSystem.pathlib.delete_tree] OK (rmtree dir)")
            return True
        except OSError as e:
            logger.error("[LocalDeviceFileSystem.pathlib.delete_tree] %s: %s", dir_path, e)
            return False

    async def delete_file(self, file_path: str) -> bool:
        file_path = self._path_mapper(file_path)
        if self._is_baas_mode:
            # BaaS-mode behavior unchanged (local unlink); ``shutil``/dir handling is
            # confined to the ``_pathlib_*`` fallback per the local-plugin arch rule.
            p = Path(file_path)
            logger.info(
                "[DEVICE-PLUGIN-DEBUG] LocalDeviceFileSystem.delete_file: %s (exists=%s)",
                file_path, p.exists(),
            )
            try:
                p.unlink(missing_ok=True)
                return True
            except OSError as e:
                logger.error("[LocalDeviceFileSystem.delete_file] %s: %s", file_path, e)
                return False
        return await self._pathlib_delete_file(file_path)

    async def _pathlib_delete_file(self, file_path: str) -> bool:
        """Delete a path (file OR directory) on the host filesystem.

        The resources delete endpoint targets files and directories (the arca/baas
        remove APIs handle both — ``delete_from_arca`` / ``/api/file/remove``), so the
        local plugin must too. ``unlink`` only removes files, so recurse on a real
        directory — mirrors ``_pathlib_delete_tree`` and the historical
        ``FileService.delete_item`` path so deleting a local folder still works.
        """
        p = Path(file_path)
        logger.info(
            "[DEVICE-PLUGIN-DEBUG] LocalDeviceFileSystem.delete_file: %s (exists=%s)",
            file_path, p.exists(),
        )
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
            return True
        except OSError as e:
            logger.error("[LocalDeviceFileSystem.delete_file] %s: %s", file_path, e)
            return False

    async def _pathlib_list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        p = Path(dir_path)
        if not p.is_dir():
            return None
        entries = p.rglob("*") if recursive else p.iterdir()
        result = []
        for entry in entries:
            result.append({
                "name": entry.name,
                "path": str(entry),
                "is_dir": entry.is_dir(),
                "relative_path": str(entry.relative_to(p)),
            })
        return result

    async def _pathlib_exists(self, path: str) -> bool:
        return Path(path).exists()

    # ── BaaS mode (filled in Task 2-9) ────────────────────────────────

    async def _baas_read_file(self, file_path: str) -> bytes | None:
        logger.info(
            "[LocalDeviceFileSystem.baas.read_file] binding=%s file=%s",
            self._binding_ctx.binding_id, file_path,
        )
        response = await self._baas_request(
            "/api/file/read", json_body={"file_path": file_path}
        )
        if response.status_code == 404:
            logger.debug(
                "[LocalDeviceFileSystem.baas.read_file] not found: %s", file_path
            )
            return None
        response.raise_for_status()
        return response.content

    async def _baas_write_file(self, file_path: str, content: bytes) -> None:
        """Write to container via /api/file/upload (multipart: file + target_path)。

        engine upload 是 multipart (UploadFile + Form target_path)，对齐 prod
        BaasDeviceFileSystem.write_file。非 2xx → raise，避免 DB 写成功但容器无文件。
        """
        logger.info(
            "[LocalDeviceFileSystem.baas.write_file] binding=%s file=%s size=%d",
            self._binding_ctx.binding_id, file_path, len(content),
        )
        info = self._baas_service.get_http_info(
            bind_id=self._binding_ctx.binding_id,
            port=self._binding_ctx.adapter_port,
            path="/api/file/upload",
            device_affinity=self._binding_ctx.entity_id,
            tenant=self._binding_ctx.tenant or None,
        )
        files = {"file": (file_path, content)}
        data = {"target_path": file_path}
        headers = {"openclawToken": info.token}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                info.http_url, files=files, data=data, headers=headers
            )
        response.raise_for_status()

    async def _baas_delete_tree(self, dir_path: str) -> bool:
        """delete_tree 幂等契约：路径不存在视为成功 (404 → True)。

        其他错（5xx / 网络 / BaaS）必 raise，让 caller 知道清理失败。
        """
        logger.info(
            "[LocalDeviceFileSystem.baas.delete_tree] binding=%s dir=%s",
            self._binding_ctx.binding_id, dir_path,
        )
        response = await self._baas_request(
            "/api/file/rmtree", json_body={"target_path": dir_path}
        )
        if response.status_code == 404:
            return True
        response.raise_for_status()
        return True

    async def _baas_list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        """List directory via container /api/file/list.

        Expected container response shape:
            {"code": 0, "data": {"files": [{"name", "path", "is_dir", "relative_path"}, ...]}}

        404 → None (path not a directory). Other errors raise per D8.
        ``recursive`` is passed through to the container; Task 7 pins the
        reverse assertion (container actually receives recursive=true).
        """
        logger.info(
            "[LocalDeviceFileSystem.baas.list_dir] binding=%s dir=%s recursive=%s",
            self._binding_ctx.binding_id, dir_path, recursive,
        )
        response = await self._baas_request(
            "/api/file/list",
            json_body={"dir_path": dir_path, "recursive": recursive},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        return body.get("data", {}).get("files", [])

    async def _baas_exists(self, path: str) -> bool:
        """exists 是 bool 契约——异常吞掉返 False，避免炸 caller。

        策略与 prod BaasDeviceFileSystem.exists 一致：先 read_file（命中文件），
        miss 再 list_dir（命中目录）。两者都 raise → 视为不存在/不可达。
        """
        try:
            content = await self._baas_read_file(path)
            if content is not None:
                return True
            files = await self._baas_list_dir(path)
            return files is not None
        except Exception as e:
            logger.warning(
                "[LocalDeviceFileSystem.baas.exists] swallowed: %s: %s", path, e
            )
            return False
