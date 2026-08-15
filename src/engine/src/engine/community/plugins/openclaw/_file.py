"""_FilePortMixin — file system port methods.

Also contains the module-level _convert_path helper and the
_PATH_PREFIX_PATTERN/_PATH_PREFIX_NEW constants (relocated from
engines/openclaw/file.py).

Addressing (#1000)
------------------
``/api/file/*`` accepts two address formats, and this module is where they are
told apart:

* **namespace-relative** — ``workspace/<rel>`` · ``identity/<rel>`` ·
  ``config`` (a leading slash is tolerated). The backend names a logical
  location and *this engine* decides where it lands, so no caller has to know
  the container layout. Resolution is bounded: the relative part may not carry
  ``..``, and the result is asserted to sit under the namespace root.
  ``workspace`` and ``identity`` are trees; ``config`` is the single
  engine-owned config file, which is why it takes no caller-chosen leaf.
* **absolute** — the OSS-view ``/aidesktop/...`` prefix (rewritten to the
  engine-view root) and already-engine-view paths (passed through). This is the
  format every current caller sends, and it keeps working byte-for-byte.

The discriminator is *first path segment ∈ the namespace set*, never the
inverse ("not ``/aidesktop`` ⇒ relative"): callers pass hardcoded
container-absolute paths such as ``/home/admin/.openclaw/workspace/skills``, and
an inverted rule would swallow them.

A **bare relative path** — one with no namespace prefix — is refused rather than
passed through. That is the one deliberate break with the old behavior: such a
path used to resolve against the engine process's CWD, so an upload reported 201
and landed outside the workspace, where neither the agent nor the NFS sync could
see it (#1000).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from engine.community.plugin_api.workspace_root import (
    workspace_root,
    workspace_root_strict,
)

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


# ── namespace-relative addressing (#1000) ────────────────────────────────────

# The engine namespaces the backend addresses files by. Mirrors the backend's
# ``core/config_compose/teclaw_paths.py`` constants — the two sides of one wire
# contract — and matches what teclaw's engine already accepts.
_WORKSPACE_NS = "workspace"
_IDENTITY_NS = "identity"
_CONFIG_NS = "config"
_ENGINE_NAMESPACES = (_WORKSPACE_NS, _IDENTITY_NS, _CONFIG_NS)

# The single file the ``config`` namespace addresses on this engine. The backend
# composes the same name today (``build_arca_config_mapper`` →
# ``{bot_engine_dir}/openclaw.json``, matching ``get_engine_config_path``); owning
# it here is what lets that mapper stop composing engine paths at all.
_CONFIG_FILENAME = "openclaw.json"


def _split_namespace(target: str) -> tuple[str, list[str]] | None:
    """Split a namespace-relative address, or return ``None`` if it is not one.

    ``"workspace/a/b.txt"`` and ``"/workspace/a/b.txt"`` both yield
    ``("workspace", ["a", "b.txt"])``; the bare namespace yields an empty
    segment list (it addresses the namespace root, which ``list_dir`` needs).
    Empty and ``"."`` segments are dropped as the noise they are — a leading
    slash or a doubled separator is not an attempt to leave the namespace.
    ``".."`` segments are kept here and refused in :func:`_resolve_namespace`,
    so the rejection names the offending input instead of silently rewriting the
    address to a different valid one.
    """
    segments = [s for s in target.split("/") if s and s != "."]
    if not segments or segments[0] not in _ENGINE_NAMESPACES:
        return None
    return segments[0], segments[1:]


def _namespace_root(namespace: str) -> Path:
    """The engine-view directory a namespace resolves against.

    openclaw's per-bot layout, as composed today by the backend's
    ``build_workspace_mapper`` / ``build_arca_identity_mapper`` /
    ``build_arca_config_mapper`` and then folded to the engine view by branches 1
    and 2 of :func:`_convert_path`:

    * ``workspace`` → ``{engine_dir}/workspace``
    * ``identity``  → ``{engine_dir}/workspace`` — openclaw keeps its identity
      files (AGENTS.md, IDENTITY.md, …) in the workspace root, not in a separate
      directory, so the two namespaces share a root on this engine.
    * ``config``    → ``{engine_dir}`` — the engine config lives beside the
      workspace as ``openclaw.json``. The namespace holds that one file; see
      :func:`_resolve_namespace`.

    The workspace root comes from :func:`workspace_root` — the engine's own
    answer to "where is my workspace", the same one ``skills``,
    ``session_files`` and ``resource_materialization`` resolve against. Deriving
    it from anything else would let a file uploaded as ``workspace/x`` land
    somewhere those services cannot see, which is the failure #1000 is about.

    Raises:
        RuntimeError: ``OPENCLAW_WORKSPACE_DIR`` is set to a relative path —
            a baas spawn-time configuration error, failed explicitly rather
            than resolved against the process CWD (same guard as branch 2).
    """
    root = workspace_root()
    if not root.is_absolute():
        raise RuntimeError(
            f"OPENCLAW_WORKSPACE_DIR must be an absolute path, got {str(root)!r}. "
            f"baas spawn-time should inject the resolved host absolute path."
        )
    if namespace == _CONFIG_NS:
        return root.parent
    return root


def _resolve_namespace(namespace: str, segments: list[str]) -> Path:
    """Join ``segments`` onto the namespace root, refusing anything that escapes.

    Containment is **lexical**: ``".."`` is refused outright and the remaining
    segments are joined onto the root, so no input can address outside it. The
    result is deliberately *not* ``resolve()``-d against the filesystem — the
    workspace legitimately contains symlinks that point outside it (the skills
    bindpaths link ``workspace/skills/skills-local`` and ``…/skills-repo`` into
    the skills pool), and following them would turn every skill file into an
    apparent escape.

    This bounds the *address*, not the process: the agent shares this
    filesystem and can reach any of it directly. The guarantee is that a path
    the backend names as ``<namespace>/…`` cannot resolve outside that
    namespace — which is what makes the engine, rather than the caller, the one
    place that decides where a logical path lands.

    ``config`` is the exception, and deliberately so: it is not a tree the
    caller populates but the one engine-owned config file, so it accepts exactly
    two spellings — the bare namespace ``config`` and the explicit
    ``config/openclaw.json`` — and refuses every other leaf. Both resolve to the
    same file. Refusing rather than resolving matters because the backend
    currently addresses this namespace with one canonical leaf for every
    provider (``config/teclaw.json``, whose real filename its arca/baas mapper
    then derives from ``engine_type`` and discards the leaf). Accepting that leaf
    verbatim here would write a stray ``teclaw.json`` beside the real config and
    report success — the config would simply never take effect, which is the same
    silent-write failure #1000 is about.

    Raises:
        ValueError: a ``".."`` segment, a ``config`` address naming anything but
            this engine's config file, or (defensively) a join that lands
            outside the root. The router maps it to 400.
    """
    if any(s == ".." for s in segments):
        raise ValueError(
            f"path escapes the {namespace!r} namespace: "
            f"{'/'.join([namespace, *segments])!r}"
        )
    if namespace == _CONFIG_NS:
        if segments not in ([], [_CONFIG_FILENAME]):
            raise ValueError(
                f"the {_CONFIG_NS!r} namespace holds this engine's config file "
                f"({_CONFIG_FILENAME!r}); address it as {_CONFIG_NS!r} or "
                f"{f'{_CONFIG_NS}/{_CONFIG_FILENAME}'!r}, got "
                f"{'/'.join([namespace, *segments])!r}"
            )
        return _namespace_root(namespace) / _CONFIG_FILENAME
    root = _namespace_root(namespace)
    resolved = root.joinpath(*segments) if segments else root
    # Invariant, not input validation: with no ".." left this cannot fail. It is
    # asserted so a future edit to the segment filter cannot quietly widen the
    # namespace.
    if resolved != root and not resolved.is_relative_to(root):
        raise ValueError(
            f"path escapes the {namespace!r} namespace: "
            f"{'/'.join([namespace, *segments])!r}"
        )
    return resolved


def _convert_path(target: str) -> Path:
    """Resolve a wire address (OSS-view or namespace-relative) to an engine path.

    Five branches (in order):
    1. 线上 ARCA pre/prod (原 regex): 折叠到 hardcode /home/admin/.openclaw/
       — 跟改造前完全一致,线上行为字节级不变。
    2. singlebox 多 bot: 折叠到 OPENCLAW_WORKSPACE_DIR 的父目录。
       env 必须由 baas spawn 时注入; regex 匹配但 env 未设 → RuntimeError
       (配置错误,显式失败而非隐式降级)。
    3. namespace-relative (#1000): ``workspace/`` · ``identity/`` · ``config/``
       (前导 ``/`` 可选) → 由本 engine 决定落点, 并断言未越出 namespace root。
    4. 其他绝对路径: passthrough(engine-view 直接路径或 desktop bot 路径)。
    5. 其他相对路径: ValueError — 旧行为会 resolve 到进程 CWD, 静默落在
       workspace 之外 (#1000)。

    The namespace branch sits **after** the two OSS-view regexes and is keyed on
    the first path segment, so every address that resolves today still resolves
    to the same place: no OSS-view or engine-view absolute path starts with a
    ``workspace`` / ``identity`` / ``config`` segment.

    Relocated from ``engines/openclaw/file.py:_convert_path``, since extended
    with the singlebox sub-branch (per-bot isolation on a shared host) and the
    namespace-relative branch (#1000).

    Raises:
        ValueError: the address is namespace-relative and escapes its namespace,
            or it is relative with no namespace prefix.
        RuntimeError: a singlebox OSS-view path with no (or a relative)
            ``OPENCLAW_WORKSPACE_DIR``.
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

    # Branch 3: namespace-relative (#1000)
    parsed = _split_namespace(target)
    if parsed is not None:
        namespace, segments = parsed
        result = _resolve_namespace(namespace, segments)
        log.info(
            "[_convert_path] branch=NAMESPACE ns=%s input=%r → %s",
            namespace, original, result,
        )
        return result

    # Branch 4: passthrough — an engine-view absolute path the caller resolved
    # itself (desktop bot paths, hardcoded container paths).
    result = Path(target)
    if result.is_absolute():
        log.info(
            "[_convert_path] branch=PASSTHROUGH input=%r → %s",
            original, result,
        )
        return result

    # Branch 5: relative with no namespace — refused. Passing it through would
    # resolve it against the engine process's CWD, which is outside the
    # workspace and outside the NFS sync pair: the write reports success and the
    # bytes are invisible to the agent and lost on container recycle (#1000).
    log.error(
        "[_convert_path] branch=RELATIVE_REJECTED input=%r → ValueError",
        original,
    )
    namespaces = ", ".join(f"{ns}/" for ns in _ENGINE_NAMESPACES)
    raise ValueError(
        f"relative path without an engine namespace: {original!r}. "
        f"Address it under one of {namespaces} — or pass an absolute "
        f"engine path."
    )


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
