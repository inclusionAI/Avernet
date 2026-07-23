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


def mirror_registered_local(
    *,
    source_root: Path,
    pool_local: Path,
    registered_local_names: list[str],
    staging_root: Path,
) -> None:
    """交换前把 registered local 精确镜像到 Pool。"""

    _remove_path(staging_root)
    staging_root.mkdir()
    for name in registered_local_names:
        staging = staging_root / name
        shutil.copytree(
            source_root / name,
            staging,
            symlinks=True,
            copy_function=shutil.copy2,
        )
        destination = pool_local / name
        _remove_path(destination)
        staging.rename(destination)
    staging_root.rmdir()


def write_baseline_manifest(
    *,
    pool_local: Path,
    registered_local_names: list[str],
    manifest_path: Path,
) -> Manifest:
    """持久化交换前 Pool 快照，供交换后或失败重试执行三方合并。"""

    manifest = snapshot_registered(
        pool_local,
        registered_local_names,
    )
    temporary = manifest_path.with_name(
        f".{manifest_path.name}.{uuid4().hex}.tmp"
    )
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
    registered_local_names: list[str],
    baseline: Manifest,
) -> dict[str, object]:
    """三方合并交换窗口内的 Legacy 变化，保留交换后的 Pool 新写入。

    ``baseline`` 是首次 final sync 后的 Pool；``source_root`` 是原子交换
    后换出的 Legacy 快照；``pool_local`` 是交换后继续承接新写入的目标。
    交换后 Pool 是唯一写入权威源：已有或已删除的目标绝不再被 Legacy
    修改/删除。只对 baseline 中不存在、且 Pool 当前仍不存在的新路径执行
    原子 no-clobber 创建；其余 Legacy delta 留在 quarantine 供审计。
    """

    source = snapshot_registered(source_root, registered_local_names)
    changed = {
        key
        for key in set(baseline) | set(source)
        if source.get(key) != baseline.get(key)
    }
    applied: list[str] = []
    conflicts: list[str] = sorted(
        key for key in changed if key not in source
    )

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
        if baseline.get(key) is not None or observed is not None:
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


def snapshot_registered(
    root: Path,
    registered_local_names: list[str],
) -> Manifest:
    manifest: Manifest = {}
    for name in registered_local_names:
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


def _remove_path(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


__all__ = [
    "load_baseline_manifest",
    "merge_post_cutover_changes",
    "mirror_registered_local",
    "snapshot_registered",
    "write_baseline_manifest",
]
