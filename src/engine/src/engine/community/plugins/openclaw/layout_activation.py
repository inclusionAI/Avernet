"""OpenClaw Skills Pool 的运行时最终同步与原子 local bridge 切换。"""

from __future__ import annotations

import ctypes
import errno
import os
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from engine.community.plugins.openclaw.layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)
from engine.community.plugins.openclaw.layout_sync import (
    load_baseline_manifest,
    merge_post_cutover_changes,
    mirror_registered_local,
    write_baseline_manifest,
)


class PoolActivationStatus(StrEnum):
    COMMITTED = "COMMITTED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    INVALID = "INVALID"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    POST_CUTOVER_SYNC_PENDING = "POST_CUTOVER_SYNC_PENDING"
    NOT_ATOMIC = "NOT_ATOMIC"


@dataclass(frozen=True, slots=True)
class SkillMapping:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class PoolActivationResult:
    status: PoolActivationStatus
    evidence: dict[str, object]

    @property
    def committed(self) -> bool:
        return self.status in {
            PoolActivationStatus.COMMITTED,
            PoolActivationStatus.ALREADY_COMMITTED,
        }

    def to_data(self) -> dict[str, object]:
        return {
            "committed": self.committed,
            "status": self.status.value,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class MappingVerificationResult:
    valid: bool
    evidence: dict[str, object]

    def to_data(self) -> dict[str, object]:
        return {"valid": self.valid, "evidence": self.evidence}


@dataclass(frozen=True, slots=True)
class MappingPublishResult:
    published: bool
    evidence: dict[str, object]

    def to_data(self) -> dict[str, object]:
        return {"published": self.published, "evidence": self.evidence}


@dataclass(frozen=True, slots=True)
class _Layout:
    legacy_root: Path
    legacy_local: Path
    pool_root: Path
    pool_local: Path
    pool_repo: Path

    @classmethod
    def for_home(cls, home: Path) -> "_Layout":
        workspace = home / ".openclaw" / "workspace"
        legacy_root = workspace / "skills"
        pool_root = workspace / "skills-pool"
        return cls(
            legacy_root=legacy_root,
            legacy_local=legacy_root / "skills-local",
            pool_root=pool_root,
            pool_local=pool_root / "skills-local",
            pool_repo=pool_root / "skills-repo",
        )


def _lexical_target(link: Path) -> Path:
    target = link.readlink()
    if not target.is_absolute():
        target = link.parent / target
    return Path(os.path.abspath(target))


def atomic_exchange_paths(left: Path, right: Path) -> bool:
    """原子交换同一文件系统上的两个目录项。

    Linux 使用 ``renameat2(RENAME_EXCHANGE)``，macOS 测试环境使用
    ``renamex_np(RENAME_SWAP)``。不支持时返回 ``False``，调用方不得降级成
    两次普通 rename。
    """

    if left.parent.stat().st_dev != right.parent.stat().st_dev:
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    left_raw = os.fsencode(left)
    right_raw = os.fsencode(right)

    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, left_raw, -100, right_raw, 2)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(left_raw, right_raw, 0x00000002)
    else:
        return False

    if result == 0:
        return True
    current_errno = ctypes.get_errno()
    if current_errno in {
        errno.ENOSYS,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        errno.EXDEV,
    }:
        return False
    raise OSError(current_errno, os.strerror(current_errno))


def _invalid(reason: str, **evidence: object) -> PoolActivationResult:
    return PoolActivationResult(
        PoolActivationStatus.INVALID,
        {"reason": reason, **evidence},
    )


def activate_openclaw_pool(
    *,
    migration_generation: str,
    preparation_id: str,
    registered_local_names: list[str],
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    repo_is_mounted: Callable[[Path], bool] | None = None,
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    """同步已登记 local，校验 mapping source，并原子提交 Legacy→Pool bridge。"""

    home_path = Path(home)
    layout = _Layout.for_home(home_path)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", migration_generation):
        return _invalid("migration_generation_invalid")

    inspection = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home_path,
        repo_is_mounted=repo_is_mounted,
    )
    if (
        inspection.status is not RuntimeLayoutInspectionStatus.READY
        or inspection.preparation_id != preparation_id
    ):
        return _invalid(
            "runtime_layout_not_ready",
            probe_status=inspection.status.value,
            preparation_id=inspection.preparation_id,
        )

    temporary = layout.legacy_root / (
        f".skills-local.pool-cutover-{migration_generation}"
    )
    quarantine = (
        layout.pool_root
        / ".migration-quarantine"
        / migration_generation
        / "skills-local"
    )
    baseline_path = layout.pool_root / (
        f".cutover-baseline-{migration_generation}.json"
    )
    normalized_names: list[str] = []
    for name in registered_local_names:
        path = Path(name)
        if (
            not name
            or path.is_absolute()
            or len(path.parts) != 1
            or name in {".", ".."}
        ):
            return _invalid("registered_local_name_invalid", name=name)
        if name not in normalized_names:
            normalized_names.append(name)
    try:
        if layout.legacy_local.is_symlink():
            if _lexical_target(layout.legacy_local) != Path(
                os.path.abspath(layout.pool_local)
            ):
                return _invalid("legacy_local_bridge_invalid")
            cleanup_pending = False
            post_sync: dict[str, object] = {}
            if temporary.is_dir() and not temporary.is_symlink():
                for name in normalized_names:
                    source = temporary / name
                    if not source.is_dir() or source.is_symlink():
                        return _invalid(
                            "registered_local_source_invalid",
                            source=str(source),
                        )
                try:
                    baseline = load_baseline_manifest(baseline_path)
                    post_sync = merge_post_cutover_changes(
                        source_root=temporary,
                        pool_local=layout.pool_local,
                        registered_local_names=normalized_names,
                        baseline=baseline,
                    )
                except (OSError, ValueError) as error:
                    return PoolActivationResult(
                        PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                        {
                            "reason": "post_cutover_sync_failed",
                            "error_type": type(error).__name__,
                            "errno": error.errno,
                        },
                    )
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                if quarantine.exists() or quarantine.is_symlink():
                    cleanup_pending = True
                else:
                    temporary.rename(quarantine)
                baseline_path.unlink(missing_ok=True)
            return PoolActivationResult(
                PoolActivationStatus.ALREADY_COMMITTED,
                {
                    "bridge": str(layout.legacy_local),
                    "target": str(layout.pool_local),
                    "quarantine": str(quarantine),
                    "quarantine_cleanup_pending": cleanup_pending,
                    "post_sync": post_sync,
                },
            )
        if not layout.legacy_local.is_dir():
            return _invalid("legacy_local_not_directory")

        for name in normalized_names:
            source = layout.legacy_local / name
            if not source.is_dir() or source.is_symlink():
                return _invalid("registered_local_source_invalid", source=str(source))

        mirror_registered_local(
            source_root=layout.legacy_local,
            pool_local=layout.pool_local,
            registered_local_names=normalized_names,
            staging_root=layout.pool_root
            / f".final-sync-{migration_generation}",
        )
        baseline = write_baseline_manifest(
            pool_local=layout.pool_local,
            registered_local_names=normalized_names,
            manifest_path=baseline_path,
        )

        targets: set[Path] = set()
        for mapping in mappings:
            source = Path(mapping.source)
            target = Path(mapping.target)
            if (
                not source.is_absolute()
                or not target.is_absolute()
                or target.parent != layout.legacy_root
                or target in targets
                or not (
                    source.is_relative_to(layout.pool_local)
                    or source.is_relative_to(layout.pool_repo)
                )
                or not source.exists()
            ):
                return _invalid(
                    "mapping_source_invalid",
                    source=str(source),
                    target=str(target),
                )
            targets.add(target)

        if temporary.exists() or temporary.is_symlink():
            return _invalid("cutover_temporary_path_occupied", path=str(temporary))
        temporary.symlink_to(layout.pool_local, target_is_directory=True)
        if not exchange_paths(layout.legacy_local, temporary):
            temporary.unlink(missing_ok=True)
            return PoolActivationResult(
                PoolActivationStatus.NOT_ATOMIC,
                {"reason": "atomic_exchange_unavailable"},
            )

        if (
            not layout.legacy_local.is_symlink()
            or _lexical_target(layout.legacy_local)
            != Path(os.path.abspath(layout.pool_local))
        ):
            return _invalid("cutover_result_ambiguous")

        if before_post_sync is not None:
            before_post_sync()
        try:
            post_sync = merge_post_cutover_changes(
                source_root=temporary,
                pool_local=layout.pool_local,
                registered_local_names=normalized_names,
                baseline=baseline,
            )
        except (OSError, ValueError) as error:
            return PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "post_cutover_sync_failed",
                    "error_type": type(error).__name__,
                    "errno": error.errno,
                },
            )

        quarantine.parent.mkdir(parents=True, exist_ok=True)
        cleanup_pending = False
        if quarantine.exists() or quarantine.is_symlink():
            cleanup_pending = True
        else:
            temporary.rename(quarantine)
        baseline_path.unlink(missing_ok=True)
        return PoolActivationResult(
            PoolActivationStatus.COMMITTED,
            {
                "bridge": str(layout.legacy_local),
                "target": str(layout.pool_local),
                "quarantine": str(quarantine),
                "quarantine_cleanup_pending": cleanup_pending,
                "registered_local_count": len(normalized_names),
                "mapping_source_count": len(mappings),
                "post_sync": post_sync,
            },
        )
    except OSError as error:
        committed = (
            layout.legacy_local.is_symlink()
            and _lexical_target(layout.legacy_local)
            == Path(os.path.abspath(layout.pool_local))
        )
        return PoolActivationResult(
            (
                PoolActivationStatus.COMMITTED
                if committed
                else PoolActivationStatus.TRANSIENT_ERROR
            ),
            {
                "reason": (
                    "post_cutover_cleanup_failed"
                    if committed
                    else "filesystem_operation_failed"
                ),
                "error_type": type(error).__name__,
                "errno": error.errno,
            },
        )


def verify_skill_mappings(
    *,
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
) -> MappingVerificationResult:
    """验证受管激活入口精确解析到请求中的 Pool source。"""

    layout = _Layout.for_home(Path(home))
    seen: set[Path] = set()
    failures: list[dict[str, str]] = []
    for mapping in mappings:
        source = Path(mapping.source)
        target = Path(mapping.target)
        reason = ""
        if (
            not source.is_absolute()
            or not (
                source.is_relative_to(layout.pool_local)
                or source.is_relative_to(layout.pool_repo)
            )
        ):
            reason = "source_outside_pool"
        elif not source.exists():
            reason = "source_missing"
        elif target.parent != layout.legacy_root or target in seen:
            reason = "target_invalid"
        elif not target.is_symlink():
            reason = "target_not_symlink"
        elif _lexical_target(target) != Path(os.path.abspath(source)):
            reason = "target_mismatch"
        seen.add(target)
        if reason:
            failures.append(
                {"source": str(source), "target": str(target), "reason": reason}
            )
    return MappingVerificationResult(
        valid=not failures,
        evidence={"checked": len(mappings), "failures": failures},
    )


def publish_pool_mappings(
    *,
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
) -> MappingPublishResult:
    """对齐已登记 Pool mapping，同时保留尚未分类的既有入口。

    #369 只掌握 Backend 已登记技能，不能把未出现在请求中的入口等同为
    stale；完整文件系统枚举与受管/外部分类由后续 #370 承接。
    """

    layout = _Layout.for_home(Path(home))
    desired: dict[Path, Path] = {}
    failures: list[dict[str, str]] = []
    for mapping in mappings:
        source = Path(mapping.source)
        target = Path(mapping.target)
        reason = ""
        if (
            not source.is_absolute()
            or not (
                source.is_relative_to(layout.pool_local)
                or source.is_relative_to(layout.pool_repo)
            )
            or not source.exists()
        ):
            reason = "source_invalid"
        elif (
            not target.is_absolute()
            or target.parent != layout.legacy_root
            or target.name in {"skills-local", "skills-repo"}
            or target in desired
        ):
            reason = "target_invalid"
        if reason:
            failures.append(
                {"source": str(source), "target": str(target), "reason": reason}
            )
        else:
            desired[target] = source
    if failures:
        return MappingPublishResult(
            published=False,
            evidence={"reason": "mapping_invalid", "failures": failures},
        )

    created: list[str] = []
    updated: list[str] = []
    kept: list[str] = []
    removed: list[str] = []
    try:
        for target, source in desired.items():
            if target.is_symlink():
                if _lexical_target(target) == Path(os.path.abspath(source)):
                    kept.append(str(target))
                    continue
                target.unlink()
                updated.append(str(target))
            elif target.exists():
                return MappingPublishResult(
                    published=False,
                    evidence={
                        "reason": "managed_target_occupied",
                        "target": str(target),
                    },
                )
            target.symlink_to(source, target_is_directory=True)
            if str(target) not in updated:
                created.append(str(target))

    except OSError as error:
        return MappingPublishResult(
            published=False,
            evidence={
                "reason": "mapping_publish_io_error",
                "error_type": type(error).__name__,
                "errno": error.errno,
            },
        )
    return MappingPublishResult(
        published=True,
        evidence={
            "total": len(desired),
            "created": created,
            "updated": updated,
            "kept": kept,
            "removed": removed,
        },
    )


__all__ = [
    "MappingVerificationResult",
    "MappingPublishResult",
    "PoolActivationResult",
    "PoolActivationStatus",
    "SkillMapping",
    "activate_openclaw_pool",
    "atomic_exchange_paths",
    "publish_pool_mappings",
    "verify_skill_mappings",
]
