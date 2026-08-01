"""_SkillsPortMixin — skills management port methods.

Also contains _SkillsEnsureError (relocated from engines/openclaw/skills.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
)
from engine.community.plugin_api.openclaw.skills import (
    PoolLayoutActivationPortResult,
)
from engine.community.plugin_api.workspace_root import workspace_root
from engine.community.plugins.openclaw._file import _convert_path
from engine.community.plugins.openclaw.layout_activation import (
    MappingSourceLayout,
    activate_openclaw_pool,
    publish_pool_mappings,
    rollback_openclaw_pool,
    verify_skill_mappings,
)
from engine.community.plugins.openclaw.layout_probe import inspect_runtime_layout
from engine.community.plugins.skills_pool.layout_quarantine import (
    cleanup_quarantine,
)
from engine.community.plugins.skills_pool.mapping_contract import (
    ResolvedMappingPayload,
    resolve_mapping_payload,
)

log = logging.getLogger("openclaw-port")


class _SkillsEnsureError(RuntimeError):
    """A single center-skill ensure failed (NAS source missing / rsync error).

    Mirrors the legacy `engines/openclaw/skills.py:_EnsureError` so
    `ensure_center_skills` soft-fails only THESE (→ a `failed` entry), while
    unexpected OSErrors still propagate (→ HTTP 500), matching legacy.
    """


class _SkillsPortMixin:
    """Domain mixin: skills sync/ensure (local-infra, no gateway/pool/token)."""

    _SKILLS_LINK_BASE_DIR_ENV = "SKILLS_LINK_BASE_DIR"
    _DEFAULT_SKILLS_LINK_BASE_DIR = "/home/admin/.extra-skills"
    _SKILLS_CENTER_NAS_ROOT_ENV = "SKILLS_CENTER_NAS_ROOT"
    _SKILLS_CENTER_LOCAL_ROOT_ENV = "SKILLS_CENTER_LOCAL_ROOT"
    _DEFAULT_SKILLS_CENTER_NAS_ROOT = "/home/admin/nfs/skills-center"
    _DEFAULT_SKILLS_CENTER_LOCAL_ROOT = str(
        workspace_root() / "skills" / "skills-center"
    )

    @staticmethod
    def _pool_mappings(
        params: dict[str, Any],
        *,
        source_layout: MappingSourceLayout,
    ) -> ResolvedMappingPayload:
        return resolve_mapping_payload(
            engine="openclaw",
            source_layout=source_layout,
            payload=params.get("mappings", []),
            mapping_contract_version=params.get("mapping_contract_version"),
        )

    async def activate_pool_layout(
        self, params: dict[str, Any]
    ) -> PoolLayoutActivationPortResult:
        result = await asyncio.to_thread(
            activate_openclaw_pool,
            migration_generation=params["migration_generation"],
            preparation_id=params["preparation_id"],
            registered_local_names=list(
                params.get("registered_local_names", [])
            ),
            mappings=list(
                self._pool_mappings(
                    params,
                    source_layout=MappingSourceLayout.POOL,
                ).mappings
            ),
        )
        return PoolLayoutActivationPortResult(**result.to_data())

    async def rollback_pool_layout(
        self, params: dict[str, Any]
    ) -> PoolLayoutActivationPortResult:
        result = await asyncio.to_thread(
            rollback_openclaw_pool,
            rollback_generation=params["rollback_generation"],
            registered_local_names=list(
                params.get("registered_local_names", [])
            ),
        )
        return PoolLayoutActivationPortResult(**result.to_data())

    async def cleanup_pool_quarantine(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            cleanup_quarantine,
            engine="openclaw",
            home=Path("/home/admin"),
            migration_generation=params["migration_generation"],
        )
        return result.to_data()

    async def probe_pool_layout(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            inspect_runtime_layout,
            engine="openclaw",
            expected_contract_version=params["layout_contract_version"],
            mapping_contract_version=MAPPING_CONTRACT_VERSION,
        )
        return result.to_data()

    async def publish_pool_mappings(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        resolved = self._pool_mappings(
            params,
            source_layout=MappingSourceLayout(
                params.get(
                    "source_layout",
                    MappingSourceLayout.POOL.value,
                )
            ),
        )
        result = await asyncio.to_thread(
            publish_pool_mappings,
            mappings=list(resolved.mappings),
            source_layout=MappingSourceLayout(
                params.get("source_layout", MappingSourceLayout.POOL.value)
            ),
        )
        data = result.to_data()
        if result.published and resolved.resolved_locators:
            data["evidence"]["resolved_mappings"] = list(
                resolved.resolved_locators
            )
        return data

    async def verify_pool_mappings(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        resolved = self._pool_mappings(
            params,
            source_layout=MappingSourceLayout(
                params.get(
                    "source_layout",
                    MappingSourceLayout.POOL.value,
                )
            ),
        )
        result = await asyncio.to_thread(
            verify_skill_mappings,
            mappings=list(resolved.mappings),
            source_layout=MappingSourceLayout(
                params.get("source_layout", MappingSourceLayout.POOL.value)
            ),
        )
        data = result.to_data()
        if result.valid and resolved.resolved_locators:
            data["evidence"]["resolved_mappings"] = list(
                resolved.resolved_locators
            )
        return data

    def _skills_resolve_base_dir(self) -> Path:
        """Resolve SKILLS_LINK_BASE_DIR from env, or default.

        Relocated from
        ``engines/openclaw/skills.py:OpenClawSkillsService._resolve_base_dir``.
        """
        base = os.getenv(
            self._SKILLS_LINK_BASE_DIR_ENV, self._DEFAULT_SKILLS_LINK_BASE_DIR,
        ).strip()
        if not base:
            base = self._DEFAULT_SKILLS_LINK_BASE_DIR
        return Path(base).expanduser().resolve()

    # Per-key asyncio locks (in-process; single-process deployment, isolated per bot).
    @classmethod
    def _get_ensure_lock(cls, key: str) -> Any:
        import asyncio as _asyncio
        from collections import defaultdict
        if not hasattr(cls, "_skills_ensure_locks_store"):
            cls._skills_ensure_locks_store: dict[str, Any] = defaultdict(
                _asyncio.Lock
            )
        return cls._skills_ensure_locks_store[key]

    @staticmethod
    def _skills_normalize_relative_path(raw: str, field: str) -> str:
        """Validate and normalise a path relative to a base dir.

        Relocated intact from
        ``engines/openclaw/skills.py:_normalize_relative_path``.
        Raises ``ValueError`` (SkillsValidationError subclass) for invalid
        paths — the adapter re-raises as-is; the router maps it to HTTP 400.
        """
        value = raw.strip()
        if not value:
            raise ValueError(f"{field} 不能为空")
        p = Path(value)
        if p.is_absolute():
            raise ValueError(f"{field} 必须是相对 base 目录的路径: {value}")
        if ".." in p.parts:
            raise ValueError(f"{field} 非法，不能包含上级路径: {value}")
        parts = [part for part in p.parts if part not in {"", "."}]
        if not parts:
            raise ValueError(f"{field} 非法: {value}")
        return Path(*parts).as_posix()

    @staticmethod
    def _skills_validate_absolute_path(raw: str, field: str) -> Path:
        """Validate that ``raw`` is a safe absolute path.

        Relocated intact from
        ``engines/openclaw/skills.py:_validate_absolute_path``.
        """
        value = raw.strip()
        if not value:
            raise ValueError(f"{field} 不能为空")
        p = Path(value)
        if not p.is_absolute():
            raise ValueError(f"{field} 必须是绝对路径: {value}")
        if ".." in p.parts:
            raise ValueError(f"{field} 非法，不能包含上级路径: {value}")
        return Path(os.path.normpath(p))

    @staticmethod
    def _skills_resolve_symlink_dest(link_path: Path) -> Path:
        """Resolve a symlink's destination to an absolute path.

        Relocated intact from
        ``engines/openclaw/skills.py:_resolve_symlink_dest``.
        """
        raw = Path(os.readlink(link_path))
        if raw.is_absolute():
            return raw.resolve(strict=False)
        return (link_path.parent / raw).resolve(strict=False)

    @staticmethod
    def _skills_rsync_dir(src: Path, dst: Path) -> None:
        """rsync src/ into dst/ (blocking).

        Relocated intact from
        ``engines/openclaw/skills.py:OpenClawSkillsService._rsync_dir``.
        """
        import subprocess as _sp

        result = _sp.run(
            ["rsync", "-rltD", "--delete", f"{src}/", f"{dst}/"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise _SkillsEnsureError(
                f"rsync exit {result.returncode}: {result.stderr.strip()[:200]}"
            )

    @staticmethod
    def _skills_ensure_current_symlink(uuid_dir: Path, version_dir_name: str) -> None:
        """Atomically update the ``current`` symlink under ``uuid_dir``.

        Relocated intact from
        ``engines/openclaw/skills.py:OpenClawSkillsService._ensure_current_symlink``.
        """
        current = uuid_dir / "current"
        if current.is_symlink() and os.readlink(current) == version_dir_name:
            return
        tmp = uuid_dir / f".current.tmp.{os.getpid()}"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(version_dir_name)
        os.replace(tmp, current)

    async def _skills_ensure_one(
        self,
        item: dict[str, Any],
        nas_root: Path,
        local_root: Path,
    ) -> None:
        """Ensure a single (skill_uuid, version) exists locally; rsync if absent.

        Relocated intact from
        ``engines/openclaw/skills.py:OpenClawSkillsService._ensure_one``
        operating on plain dicts.  Raises ``RuntimeError`` on NAS-source-
        missing or rsync failure.
        """
        import asyncio as _asyncio

        skill_uuid = item["skill_uuid"]
        version_dir_name = str(item["version"])
        local_uuid_dir = local_root / skill_uuid
        local_version_dir = local_uuid_dir / version_dir_name

        if local_version_dir.exists() and any(local_version_dir.iterdir()):
            self._skills_ensure_current_symlink(local_uuid_dir, version_dir_name)
            return

        lock_key = f"{skill_uuid}/{version_dir_name}"
        lock = self._get_ensure_lock(lock_key)
        async with lock:
            if local_version_dir.exists() and any(local_version_dir.iterdir()):
                self._skills_ensure_current_symlink(local_uuid_dir, version_dir_name)
                return

            nas_version_dir = nas_root / skill_uuid / version_dir_name
            if not nas_version_dir.exists():
                raise _SkillsEnsureError(f"NAS source missing: {nas_version_dir}")

            local_version_dir.parent.mkdir(parents=True, exist_ok=True)
            await _asyncio.to_thread(
                self._skills_rsync_dir, nas_version_dir, local_version_dir
            )
            self._skills_ensure_current_symlink(local_uuid_dir, version_dir_name)

    async def ensure_center_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        """Ensure each (skill_uuid, version) from ``params["items"]`` is present locally.

        ``params`` keys: ``items`` (list[dict] each with ``skill_uuid``,
        ``version``).
        Returns dict with keys ``ok`` (list[dict]) and ``failed``
        (list[dict] each with ``skill_uuid``, ``version``, ``reason``).
        Relocated intact from
        ``engines/openclaw/skills.py:OpenClawSkillsService.ensure_center_skills``.
        """
        log.info("[skills.ensure] start")
        nas_root = Path(os.environ.get(
            self._SKILLS_CENTER_NAS_ROOT_ENV, self._DEFAULT_SKILLS_CENTER_NAS_ROOT
        ))
        local_root = Path(os.environ.get(
            self._SKILLS_CENTER_LOCAL_ROOT_ENV, self._DEFAULT_SKILLS_CENTER_LOCAL_ROOT
        ))

        ok: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for item in params.get("items", []):
            try:
                await self._skills_ensure_one(item, nas_root, local_root)
                ok.append(item)
            except _SkillsEnsureError as e:
                # Soft-fail ONLY explicit ensure errors (NAS-missing / rsync),
                # matching legacy `_EnsureError`. Unexpected OSErrors propagate
                # (→ HTTP 500) rather than being silently logged as failures.
                log.warning(
                    "[skills.ensure] failed uuid=%s ver=%s: %s",
                    item.get("skill_uuid"), item.get("version"), e,
                )
                failed.append({
                    "skill_uuid": item.get("skill_uuid", ""),
                    "version": item.get("version", ""),
                    "reason": str(e),
                })

        log.info(
            "[skills.ensure] done total=%d ok=%d failed=%d",
            len(params.get("items", [])), len(ok), len(failed),
        )
        return {"ok": ok, "failed": failed}

    async def sync_symlinks(self, params: dict[str, Any]) -> dict[str, Any]:
        """Reconcile relative-path symlinks under the base dir.

        ``params`` keys: ``symlinks`` (list[dict] each with ``source``,
        ``target``).
        Returns dict with ``total``, ``created``, ``updated``, ``kept``,
        ``removed``, ``base_dir``.
        Relocated intact from
        ``engines/openclaw/skills.py:OpenClawSkillsService.sync_symlinks``.
        """
        requested = params.get("symlinks") or []
        log.info("[skills.symlink] start sync requested_count=%d", len(requested))

        base_dir = self._skills_resolve_base_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        if not base_dir.is_dir():
            raise RuntimeError(f"base 目录无效: {base_dir}")

        desired: dict[str, Path] = {}
        for item in requested:
            source = self._skills_normalize_relative_path(item["source"], "source")
            target = self._skills_normalize_relative_path(item["target"], "target")
            if target in desired:
                raise ValueError(f"target 重复: {target}")
            desired[target] = (base_dir / source).resolve(strict=False)

        created: list[str] = []
        kept: list[str] = []
        removed: list[str] = []
        updated: list[str] = []
        to_recreate: set[str] = set()

        for entry in base_dir.rglob("*"):
            if not entry.is_symlink():
                continue
            name = entry.relative_to(base_dir).as_posix()
            if name not in desired:
                entry.unlink()
                removed.append(name)
                continue
            current_dest = self._skills_resolve_symlink_dest(entry)
            desired_dest = desired[name]
            if current_dest == desired_dest:
                kept.append(name)
                continue
            entry.unlink()
            to_recreate.add(name)

        for target, source_path in desired.items():
            if target in kept:
                continue
            link_path = base_dir / target
            link_path.parent.mkdir(parents=True, exist_ok=True)
            if link_path.exists() and not link_path.is_symlink():
                raise RuntimeError(f"target 已被非软链文件占用: {link_path}")
            if link_path.is_symlink():
                link_path.unlink()
                to_recreate.add(target)
            link_path.symlink_to(source_path)
            (updated if target in to_recreate else created).append(target)

        log.info(
            "[skills.symlink] sync done total=%d created=%d updated=%d kept=%d removed=%d",
            len(desired), len(created), len(updated), len(kept), len(removed),
        )
        return {
            "total": len(desired),
            "created": created,
            "updated": updated,
            "kept": kept,
            "removed": removed,
            "base_dir": str(base_dir),
        }

    async def sync_bindpaths(self, params: dict[str, Any]) -> dict[str, Any]:
        """Reconcile absolute-path symlinks.

        ``params`` keys: ``symlinks`` (list[dict] each with ``source``,
        ``target``), ``clean_target_dir`` (bool, default True).
        Returns dict with ``total``, ``created``, ``updated``, ``kept``,
        ``removed``.
        Relocated intact from
        ``engines/openclaw/skills.py:OpenClawSkillsService.sync_bindpaths``.
        """
        requested = params.get("symlinks") or []
        clean_target_dir = params.get("clean_target_dir", True)
        log.info(
            "[skills.bindpath] start sync requested_count=%d clean_target_dir=%s",
            len(requested), clean_target_dir,
        )

        desired: dict[Path, Path] = {}
        for item in requested:
            source_raw = self._skills_validate_absolute_path(item["source"], "source")
            target_raw = self._skills_validate_absolute_path(item["target"], "target")
            # source/target 都可能是 OSS-view 抽象路径
            # (/aidesktop/aidesktop_(pre|prod|singlebox)/...) — 必须翻译成
            # engine-view 宿主路径再 mkdir/symlink_to。改造前 _FilePortMixin.upload 等已经
            # 在调用点先转, sync_bindpaths 当时漏了一处。
            # singlebox 多 bot 场景下,source 也是 OSS-view (skills-local/skills-repo 在
            # per-bot workspace 下), 不转的话软链会指向不存在的 /aidesktop/... 路径。
            source = _convert_path(str(source_raw))
            target = _convert_path(str(target_raw))
            log.info(
                "[skills.bindpath] item source_raw=%s → %s target_raw=%s → %s",
                source_raw, source, target_raw, target,
            )
            if target in desired:
                raise ValueError(f"target 重复: {target}")
            desired[target] = source

        created: list[str] = []
        kept: list[str] = []
        updated: list[str] = []
        removed: list[str] = []
        to_recreate: set[Path] = set()

        # Validate the complete desired set before mutating any target. This
        # boundary is shared by direct CRUD sync and restart reconciliation;
        # rejecting here prevents both paths from creating dangling active
        # links. The source can disappear after this check, but that unavoidable
        # filesystem race is narrower than committing a known-missing source.
        missing_sources = sorted(
            {str(source) for source in desired.values() if not source.exists()}
        )
        if missing_sources:
            raise RuntimeError(
                "bindpath source does not exist: " + ", ".join(missing_sources)
            )

        for target, source in desired.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                current_dest = self._skills_resolve_symlink_dest(target)
                if current_dest == source.resolve(strict=False):
                    kept.append(str(target))
                    continue
                target.unlink()
                to_recreate.add(target)
            elif target.exists():
                raise RuntimeError(f"target 已被非软链文件占用: {target}")
            target.symlink_to(source)
            (updated if target in to_recreate else created).append(str(target))

        if clean_target_dir:
            target_dirs = {t.parent for t in desired}
            reserved_layout_bridges = {"skills-local", "skills-repo"}
            for d in target_dirs:
                if not d.is_dir():
                    continue
                for entry in d.iterdir():
                    if not entry.is_symlink():
                        continue
                    if entry.name in reserved_layout_bridges:
                        continue
                    if entry not in desired:
                        entry.unlink()
                        removed.append(str(entry))

        log.info(
            "[skills.bindpath] sync done total=%d created=%d updated=%d kept=%d removed=%d",
            len(desired), len(created), len(updated), len(kept), len(removed),
        )
        return {
            "total": len(desired),
            "created": created,
            "updated": updated,
            "kept": kept,
            "removed": removed,
        }

    async def clean_symlinks(self, params: dict[str, Any]) -> dict[str, Any]:
        """Remove every symlink under each directory in ``params["directories"]``.

        Raises ``ValueError`` when ``directories`` is empty.
        Returns dict with ``directories_scanned`` (int), ``removed`` (list[str]).
        Relocated intact from
        ``engines/openclaw/skills.py:OpenClawSkillsService.clean_symlinks``.
        """
        directories = params.get("directories") or []
        if not directories:
            raise ValueError("directories 不能为空")

        removed: list[str] = []
        directories_scanned = 0

        for raw_dir in directories:
            dir_raw = self._skills_validate_absolute_path(raw_dir, "directory")
            dir_path = _convert_path(str(dir_raw))
            log.info(
                "[skills.clean] dir_raw=%s → %s",
                dir_raw, dir_path,
            )
            if not dir_path.is_dir():
                continue
            directories_scanned += 1
            for entry in dir_path.iterdir():
                if entry.is_symlink():
                    entry.unlink()
                    removed.append(str(entry))

        log.info(
            "[skills.clean] scanned=%d removed=%d",
            directories_scanned, len(removed),
        )
        return {"directories_scanned": directories_scanned, "removed": removed}
