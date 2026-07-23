"""Skills Pool cutover 前后 local 数据同步原语。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from uuid import uuid4

from engine.community.plugins.skills_pool.layout_atomic import (
    atomic_exchange_paths,
)

Fingerprint = tuple[str, str]
Manifest = dict[str, Fingerprint]


def mirror_local_tree(
    *,
    source_root: Path,
    pool_local: Path,
    staging_root: Path,
) -> list[str]:
    """交换前把 Legacy local 的完整文件系统真相精确镜像到 Pool。"""

    _remove_path(staging_root)
    staging_root.mkdir()
    source_entries = sorted(source_root.iterdir(), key=lambda path: path.name)
    for source in source_entries:
        _copy_entry(source, staging_root / source.name)

    for destination in list(pool_local.iterdir()):
        _remove_path(destination)
    for staged in list(staging_root.iterdir()):
        staged.rename(pool_local / staged.name)
    staging_root.rmdir()
    return [entry.name for entry in source_entries]


def write_baseline_manifest(
    *,
    pool_local: Path,
    local_names: list[str],
    manifest_path: Path,
) -> Manifest:
    """持久化交换前 Pool 快照，供交换后或失败重试执行三方合并。"""

    manifest = snapshot_local(pool_local, local_names=local_names)
    temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps({key: list(value) for key, value in manifest.items()}),
        encoding="utf-8",
    )
    os.replace(temporary, manifest_path)
    return manifest


def load_baseline_manifest(manifest_path: Path) -> Manifest:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("cutover baseline must be an object")
    manifest: Manifest = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError("cutover baseline entry is invalid")
        manifest[key] = (value[0], value[1])
    return manifest


def merge_post_cutover_changes(
    *,
    source_root: Path,
    pool_local: Path,
    baseline: Manifest,
) -> dict[str, object]:
    """三方合并交换窗口内的 Legacy 变化，保留交换后的 Pool 新写入。

    ``baseline`` 是首次 final sync 后的 Pool；``source_root`` 是原子交换
    后换出的 Legacy 快照；``pool_local`` 是交换后继续承接新写入的目标。
    交换后 Pool 是唯一写入权威源：新路径使用原子 no-clobber 创建；已有
    路径使用原子 exchange 检查交换瞬间换出的实际版本，Pool 已变化时立即
    回滚并保留 Pool。删除和无法安全收敛的 delta 留在 quarantine 供审计。

    这是本期明确接受的 best-effort 边界：没有写栅栏时，跨 exchange 持有
    的已打开文件描述符，或连续命中回滚后再次交换窗口的同文件写入，仍可能
    落到随后清理的临时 inode。完整消除此竞态需要所有 writer 遵守共享的
    quiesce/lock 协议，不属于 #370。
    """

    source = snapshot_local(source_root)
    changed = {
        key
        for key in set(baseline) | set(source)
        if source.get(key) != baseline.get(key)
    }
    applied: list[str] = []
    conflicts: list[str] = sorted(key for key in changed if key not in source)

    upserts = sorted(
        (key for key in changed if key in source),
        key=lambda key: (key.count("/"), key),
    )
    for key in upserts:
        source_path = source_root / key
        target = pool_local / key
        desired = source[key]
        observed = _fingerprint(target)
        if observed == desired:
            continue
        baseline_fingerprint = baseline.get(key)
        if baseline_fingerprint is not None:
            if observed == baseline_fingerprint and _replace_if_unchanged(
                source=source_path,
                target=target,
                expected=baseline_fingerprint,
                desired=desired,
            ):
                applied.append(key)
            else:
                conflicts.append(key)
            continue
        if observed is not None:
            conflicts.append(key)
            continue
        if not _create_if_absent(
            source=source_path,
            target=target,
            desired=desired,
        ):
            conflicts.append(key)
            continue
        applied.append(key)

    return {
        "applied": applied,
        "conflicts_preserved_in_pool": conflicts,
    }


def snapshot_local(
    root: Path,
    local_names: list[str] | None = None,
) -> Manifest:
    manifest: Manifest = {}
    names = (
        local_names
        if local_names is not None
        else sorted(entry.name for entry in root.iterdir())
    )
    for name in names:
        skill_root = root / name
        root_fingerprint = _fingerprint(skill_root)
        if root_fingerprint is None:
            continue
        manifest[name] = root_fingerprint
        if root_fingerprint[0] != "dir":
            continue
        for current_root, directory_names, file_names in os.walk(
            skill_root,
            followlinks=False,
        ):
            current_path = Path(current_root)
            for entry_name in [*directory_names, *file_names]:
                entry = current_path / entry_name
                fingerprint = _fingerprint(entry)
                if fingerprint is not None:
                    manifest[entry.relative_to(root).as_posix()] = fingerprint
    return manifest


def _copy_entry(source: Path, destination: Path) -> None:
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            copy_function=shutil.copy2,
        )
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def _fingerprint(path: Path) -> Fingerprint | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if stat.S_ISDIR(mode):
        return ("dir", "")
    if stat.S_ISLNK(mode):
        return ("symlink", os.readlink(path))
    if stat.S_ISREG(mode):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return ("file", digest.hexdigest())
    raise OSError(f"unsupported filesystem entry: {path}")


def _create_if_absent(
    *,
    source: Path,
    target: Path,
    desired: Fingerprint,
) -> bool:
    if _fingerprint(target) is not None:
        return False
    if not target.parent.is_dir() or target.parent.is_symlink():
        return False
    kind = desired[0]
    if kind == "dir":
        try:
            target.mkdir()
        except (FileExistsError, FileNotFoundError, NotADirectoryError):
            return False
        return _fingerprint(target) == desired

    temporary = target.with_name(f".{target.name}.{uuid4().hex}.pool-sync")
    try:
        if kind == "file":
            shutil.copy2(source, temporary)
        elif kind == "symlink":
            temporary.symlink_to(os.readlink(source))
        else:
            raise OSError(f"unsupported manifest kind: {kind}")
    except (FileNotFoundError, NotADirectoryError):
        return False

    if _fingerprint(target) is not None:
        _remove_path(temporary)
        return False
    try:
        if kind == "file":
            os.link(temporary, target)
            temporary.unlink()
        else:
            target.symlink_to(os.readlink(temporary))
            temporary.unlink()
    except (FileExistsError, FileNotFoundError, NotADirectoryError):
        _remove_path(temporary)
        return False
    return _fingerprint(target) == desired


def _replace_if_unchanged(
    *,
    source: Path,
    target: Path,
    expected: Fingerprint,
    desired: Fingerprint,
) -> bool:
    """以 exchange 瞬间换出的对象判断 Pool 是否仍为 baseline。

    先交换再检查，避免 check-then-replace 的 TOCTOU。若换出的 Pool 已变化，
    立即原子换回；若回滚窗口内 canonical path 又收到写入，则该更晚写入
    随临时项再次换回 canonical。调用方接受无协作写锁时跨 inode 写入仍有
    极窄残余竞态；此处不声称提供文件内容 CAS。
    """

    if desired == expected:
        return True
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.pool-sync")
    try:
        if desired[0] == "file":
            shutil.copy2(source, temporary)
        elif desired[0] == "symlink":
            temporary.symlink_to(os.readlink(source))
        elif desired[0] == "dir":
            temporary.mkdir()
        else:
            return False
        if not atomic_exchange_paths(temporary, target):
            raise OSError("atomic file exchange unavailable")
        displaced = _fingerprint(temporary)
        if displaced == expected:
            return _fingerprint(target) == desired

        if not atomic_exchange_paths(temporary, target):
            raise OSError("failed to restore concurrently changed Pool file")
        candidate_after_rollback = _fingerprint(temporary)
        if candidate_after_rollback != desired:
            if not atomic_exchange_paths(temporary, target):
                raise OSError("failed to restore newer Pool file")
        return _fingerprint(target) == desired
    except (FileNotFoundError, NotADirectoryError):
        return False
    finally:
        _remove_path(temporary)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


__all__ = [
    "load_baseline_manifest",
    "merge_post_cutover_changes",
    "mirror_local_tree",
    "snapshot_local",
    "write_baseline_manifest",
]
