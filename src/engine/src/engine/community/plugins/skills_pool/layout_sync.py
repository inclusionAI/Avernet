"""Skills Pool cutover 前后 local 数据同步原语。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from uuid import uuid4

Fingerprint = tuple[str, str]
Manifest = dict[str, Fingerprint]


def mirror_local_tree(
    *,
    source_root: Path,
    pool_local: Path,
    staging_root: Path,
) -> tuple[list[str], Manifest]:
    """把 Legacy local 收敛到 Pool，同时保留同步窗口内的 Pool 新写入。

    调用前 Backend 已开始把路径消费者切到 canonical Pool。先记录 Pool
    baseline，再把 Legacy 复制到 staging，最后做三方 best-effort 合并：
    Pool 仍等于 baseline 的路径应用 Legacy；已变化的路径保留 Pool。Legacy
    已删除且 Pool 未变化的路径才会被清理。
    """

    pool_baseline = snapshot_local(pool_local)
    _remove_path(staging_root)
    staging_root.mkdir()
    source_entries = sorted(source_root.iterdir(), key=lambda path: path.name)
    for source in source_entries:
        _copy_entry(source, staging_root / source.name)

    staged_manifest = snapshot_local(staging_root)
    merge_post_cutover_changes(
        source_root=staging_root,
        pool_local=pool_local,
        baseline=pool_baseline,
    )
    deleted = sorted(
        set(pool_baseline) - set(staged_manifest),
        key=lambda key: (-key.count("/"), key),
    )
    for key in deleted:
        _remove_if_unchanged(
            target=pool_local / key,
            expected=pool_baseline[key],
        )
    _remove_path(staging_root)
    return [entry.name for entry in source_entries], staged_manifest


def write_baseline_manifest(
    *,
    pool_local: Path,
    local_names: list[str],
    manifest_path: Path,
    manifest: Manifest | None = None,
) -> Manifest:
    """持久化 retire 前的 Legacy 快照，供失败重试执行三方合并。

    ``pool_local`` 参数名为向前兼容保留；调用方传入的是 final sync 完成后
    尚未退役的 Legacy local。Pool 可能已承接 Backend 新写入，不能作为
    Legacy 窗口变化的比较基线。
    """

    effective_manifest = (
        manifest
        if manifest is not None
        else snapshot_local(pool_local, local_names=local_names)
    )
    temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(
            {key: list(value) for key, value in effective_manifest.items()}
        ),
        encoding="utf-8",
    )
    os.replace(temporary, manifest_path)
    return effective_manifest


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
    """三方合并 rename 窗口内的 Legacy 变化，保留 Pool 新写入。

    ``baseline`` 是首次 staging copy 得到的 Legacy 快照；``source_root``
    是普通 rename 后进入 quarantine 的 Legacy；``pool_local`` 已承接
    Backend 新写入。新路径使用 no-clobber 创建；已有路径仅在 Pool 仍等于
    baseline 时以普通 replace 发布。Pool 已变化时保留 Pool，无法安全收敛
    的 delta 留在 quarantine 供审计。

    这是本期明确接受的 best-effort 边界：没有共享写栅栏时，fingerprint
    校验与 replace 之间仍有极窄 TOCTOU。完整消除此竞态需要所有 writer
    遵守 quiesce/lock 协议，不属于本期。
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
    """当 Pool 仍为 baseline 时，以普通 rename 发布 Legacy 窗口变化。

    目标 NAS/FUSE 不支持 ``RENAME_EXCHANGE``，因此这里在最后一次 fingerprint
    校验后使用同文件系统 ``os.replace``。校验与 replace 之间仍存在极窄
    TOCTOU；这是无共享写栅栏方案明确接受的 best-effort 边界。若校验时
    Pool 已变化，则保留 Pool 权威版本并把 Legacy 变化留在 quarantine。
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
        if _fingerprint(target) != expected:
            return False
        try:
            os.replace(temporary, target)
        except (IsADirectoryError, NotADirectoryError):
            # POSIX rename cannot replace across entry kinds on every
            # filesystem. Recheck above, then remove only the exact baseline
            # entry; a non-empty concurrently changed directory is preserved.
            if expected[0] == "dir":
                target.rmdir()
            else:
                target.unlink()
            os.replace(temporary, target)
        return _fingerprint(target) == desired
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    finally:
        _remove_path(temporary)


def _remove_if_unchanged(*, target: Path, expected: Fingerprint) -> bool:
    """仅清理仍等于同步前 baseline 的 Legacy deletion。"""

    if _fingerprint(target) != expected:
        return False
    try:
        if expected[0] == "dir":
            target.rmdir()
        else:
            target.unlink()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    return _fingerprint(target) is None


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
