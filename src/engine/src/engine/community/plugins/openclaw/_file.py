"""_FilePortMixin — file system port methods.

Also contains the module-level _convert_path helper and the
_PATH_PREFIX_PATTERN/_PATH_PREFIX_NEW constants (relocated from
engines/openclaw/file.py).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from engine.community.plugin_api.workspace_root import workspace_root_strict

log = logging.getLogger("openclaw-port")


# ── file helpers (relocated from engines/openclaw/file.py) ───────────────────

_PATH_PREFIX_PATTERN = re.compile(
    r"^/aidesktop/aidesktop_(?:pre|prod)/bolt_data/[^/]+/[^/]+/openclaw/"
)
_PATH_PREFIX_NEW = "/home/admin/.openclaw/"

# 新增 singlebox 专用 regex — 仅在 baas 注入 OPENCLAW_WORKSPACE_DIR 时启用
_SINGLEBOX_PATH_PATTERN = re.compile(
    r"^/aidesktop/aidesktop_singlebox/bolt_data/[^/]+/[^/]+/openclaw/"
)


def _convert_path(target: str) -> Path:
    """Rewrite OSS-view path prefix to engine-view per-bot path.

    Three branches (in order):
    1. 线上 ARCA pre/prod (原 regex): 折叠到 hardcode /home/admin/.openclaw/
       — 跟改造前完全一致,线上行为字节级不变。
    2. singlebox 多 bot: 折叠到 OPENCLAW_WORKSPACE_DIR 的父目录。
       env 必须由 baas spawn 时注入; regex 匹配但 env 未设 → RuntimeError
       (配置错误,显式失败而非隐式降级)。
    3. 其他: passthrough(engine-view 直接路径或 desktop bot 路径)。

    Relocated intact from ``engines/openclaw/file.py:_convert_path``,本次
    扩展加入 singlebox 副分支以支持多 bot 共宿主下的 per-bot 隔离。
    """
    original = target
    target = target.strip()

    # Branch 1: 线上 OSS-view (pre|prod) — 改造前行为保留
    match = _PATH_PREFIX_PATTERN.match(target)
    if match:
        new_target = _PATH_PREFIX_NEW + target[match.end():]
        result = Path(new_target).expanduser()
        log.info(
            "[_convert_path] branch=PROD_OSS input=%r → %s",
            original, result,
        )
        return result

    # Branch 2: singlebox OSS-view — 仅在 env 注入时启用
    match = _SINGLEBOX_PATH_PATTERN.match(target)
    if match:
        env_root = workspace_root_strict()
        if env_root is None:
            log.error(
                "[_convert_path] branch=SINGLEBOX_ERR input=%r env=UNSET → RuntimeError",
                original,
            )
            raise RuntimeError(
                f"Got singlebox OSS-view path but OPENCLAW_WORKSPACE_DIR not set: "
                f"{target!r}. This is a baas spawn-time configuration error."
            )
        if not env_root.is_absolute():
            log.error(
                "[_convert_path] branch=SINGLEBOX_ERR input=%r env=%s(not absolute) → RuntimeError",
                original, env_root,
            )
            raise RuntimeError(
                f"OPENCLAW_WORKSPACE_DIR must be an absolute path, got {str(env_root)!r}. "
                f"baas spawn-time should inject the resolved host absolute path."
            )
        # env_root = /Users/.../bolt_data/staff_X/bot-Y/openclaw/workspace
        # .parent  = /Users/.../bolt_data/staff_X/bot-Y/openclaw
        # 加 / 让前缀对称于 _PATH_PREFIX_NEW
        new_prefix = str(env_root.parent) + "/"
        new_target = new_prefix + target[match.end():]
        result = Path(new_target).expanduser()
        log.info(
            "[_convert_path] branch=SINGLEBOX_OSS input=%r env_root=%s → %s",
            original, env_root, result,
        )
        return result

    # Branch 3: passthrough
    result = Path(target)
    log.info(
        "[_convert_path] branch=PASSTHROUGH input=%r → %s",
        original, result,
    )
    return result


class _FilePortMixin:
    """Domain mixin: file system operations (local-infra, no gateway/pool/token)."""

    async def upload(self, target_path: str, content: bytes) -> dict[str, Any]:
        """Write ``content`` to ``target_path`` after path rewrite.

        Returns primitive dict with ``target_path``, ``size``, ``overwritten``.
        Relocated from ``engines/openclaw/file.py:OpenClawFileService.upload``.
        """
        target = (target_path or "").strip()
        if not target:
            raise ValueError("目标路径不能为空")

        final_path = _convert_path(target)
        if final_path.exists() and final_path.is_dir():
            raise IsADirectoryError(f"目标路径已被目录占用: {final_path}")

        final_path.parent.mkdir(parents=True, exist_ok=True)
        is_overwrite = final_path.exists() and final_path.is_file()
        final_path.write_bytes(content)
        return {
            "target_path": str(final_path),
            "size": final_path.stat().st_size,
            "overwritten": is_overwrite,
        }

    async def read(self, file_path: str) -> bytes:
        """Read file at ``file_path`` (after path rewrite) as bytes.

        Returns ``b""`` for an empty path.
        Relocated from ``engines/openclaw/file.py:OpenClawFileService.read``.
        """
        target = (file_path or "").strip()
        if not target:
            return b""
        final_path = _convert_path(target)
        if not final_path.exists() or not final_path.is_file():
            raise FileNotFoundError(f"文件不存在: {final_path}")
        return final_path.read_bytes()

    async def remove(self, target_path: str) -> dict[str, Any]:
        """Delete file or directory at ``target_path``.

        Returns primitive dict with ``target_path``, ``path_type``.
        Relocated from ``engines/openclaw/file.py:OpenClawFileService.remove``.
        """
        target = (target_path or "").strip()
        if not target:
            raise ValueError("目标路径不能为空")
        final_path = _convert_path(target)
        if not final_path.exists():
            raise FileNotFoundError(f"路径不存在: {final_path}")

        if final_path.is_file():
            final_path.unlink()
            return {"target_path": str(final_path), "path_type": "file"}
        if final_path.is_dir():
            shutil.rmtree(final_path)
            return {"target_path": str(final_path), "path_type": "directory"}
        raise ValueError(f"不支持的路径类型: {final_path}")

    async def rmtree(self, target_path: str) -> str:
        """Recursively remove directory at ``target_path``; return resolved path.

        Relocated from ``engines/openclaw/file.py:OpenClawFileService.rmtree``.
        """
        target = (target_path or "").strip()
        if not target:
            raise ValueError("目标路径不能为空")
        final_path = _convert_path(target)
        if not final_path.exists():
            raise FileNotFoundError(f"路径不存在: {final_path}")
        # symlink-to-dir 也满足 is_dir() (follow_symlinks=True 默认),但
        # shutil.rmtree(symlink) 会 raise "Cannot call rmtree on a symbolic link"。
        # 单独判 symlink → unlink (只删链不删目标),保留源目录子树。
        # singlebox 移除 skill 软链 (TC-CAP-C016) 走这条路径。
        if final_path.is_symlink():
            final_path.unlink()
            return str(final_path)
        # 单文件也接 — backend Protocol 的 ``delete_tree`` 是个误导命名,调用方
        # ``FileService.delete_item`` 把单文件 path 也丢进来 (前端"删文件"/
        # "删文件夹"复用同一端点)。原行为对单文件 raise NotADirectoryError →
        # backend 500 → HTTP 层 404 "File not found"。
        # singlebox 资源管理删文件 (TC-RSRC-C001/A009) 走这条路径。
        if final_path.is_file():
            final_path.unlink()
            return str(final_path)
        if not final_path.is_dir():
            raise NotADirectoryError(f"路径不是目录: {final_path}")
        shutil.rmtree(final_path)
        return str(final_path)

    async def list_dir(
        self, dir_path: str, recursive: bool = False, exclude_dirs: set[str] | None = None
    ) -> dict[str, Any]:
        """List contents of ``dir_path``; returns primitive dict with ``files`` list.

        Each file entry is a dict with ``name``, ``path``, ``relative_path``,
        ``is_dir``, ``size``.
        Relocated from ``engines/openclaw/file.py:OpenClawFileService.list_dir``.
        """
        target = (dir_path or "").strip()
        if not target:
            raise ValueError("目录路径不能为空")
        final_path = _convert_path(target)
        if not final_path.exists():
            raise FileNotFoundError(f"目录不存在: {final_path}")
        if not final_path.is_dir():
            raise NotADirectoryError(f"路径不是目录: {final_path}")

        files: list[dict[str, Any]] = []
        if recursive:
            for root, dirs, filenames in os.walk(final_path):
                for filename in filenames:
                    fp = Path(root) / filename
                    files.append({
                        "name": filename,
                        "path": str(fp),
                        "relative_path": str(fp.relative_to(final_path)),
                        "is_dir": False,
                        "size": fp.stat().st_size,
                    })
                for dirname in dirs:
                    dp = Path(root) / dirname
                    files.append({
                        "name": dirname,
                        "path": str(dp),
                        "relative_path": str(dp.relative_to(final_path)),
                        "is_dir": True,
                        "size": 0,
                    })
        else:
            for item in final_path.iterdir():
                files.append({
                    "name": item.name,
                    "path": str(item),
                    "relative_path": str(item.relative_to(final_path)),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                })

        return {
            "dir_path": str(final_path),
            "recursive": recursive,
            "files": files,
        }
