"""Fixed isolated worker: count entries with confined symlink traversal.

This module is also directly executable; only stdlib imports are needed.
"""
from __future__ import annotations

import errno
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys


class ScanError(Exception):
    """Safe worker error identifier."""


class SkipLink(Exception):
    """An abnormal link contributes zero, without hiding ordinary scan errors."""


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino


@contextmanager
def _resolve(path: Path, roots: tuple[Path, ...], hops=0):
    """Retain and verify all opened ancestors, including across link expansion."""
    descriptors = [os.open("/", os.O_RDONLY | os.O_DIRECTORY)]
    ancestors = []
    current = Path("/")
    try:
        for index, component in enumerate(path.parts[1:]):
            parent = descriptors[-1]
            next_path = current.parent if component == ".." else current / component
            # COSEC: resolve links before '..'; never inspect an unrelated subtree.
            if not any(next_path.is_relative_to(root) or root.is_relative_to(next_path) for root in roots):
                raise SkipLink("outside")
            metadata = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                # COSEC: trusted root ancestry itself must never be redirected.
                if not any((current / component).is_relative_to(root) and current / component != root for root in roots):
                    raise ScanError("path_forbidden")
                if hops >= 40:
                    raise SkipLink("cycle")
                target = os.readlink(component, dir_fd=parent)
                _same_entry(parent, component, metadata)
                destination = current / target
                destination = destination.joinpath(*path.parts[index + 2:])
                resolved_target = False
                try:
                    with _resolve(destination, roots, hops + 1) as resolved:
                        resolved_target = True
                        yield resolved
                except (FileNotFoundError, NotADirectoryError):
                    if resolved_target:
                        raise
                    raise SkipLink("dangling") from None
                _same_entry(parent, component, metadata)
                break
            current = next_path
            if stat.S_ISDIR(metadata.st_mode):
                child = _open_directory(parent, component)
                descriptors.append(child)
                if _identity(os.fstat(child)) != _identity(metadata):
                    raise ScanError("directory_changed")
                ancestors.append((parent, component, metadata))
            elif index != len(path.parts) - 2:
                raise NotADirectoryError
            else:
                yield None, metadata, current
                _same_entry(parent, component, metadata)
                break
        else:
            if not any(current.is_relative_to(root) for root in roots):
                raise SkipLink("outside")
            yield descriptors[-1], os.fstat(descriptors[-1]), current
        for parent, component, metadata in ancestors:
            _same_entry(parent, component, metadata)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_directory(parent: int, name: str) -> int:
    # 以下为安全注释COSEC：每段路径都相对可信 fd 打开并拒绝链接，防止校验后替换逃逸。
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            if stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
                raise ScanError("path_forbidden") from None
        raise


def _same_entry(parent: int, name: str, original: os.stat_result) -> None:
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
        raise ScanError("directory_changed")


def _scan(directory: int, physical: Path, roots: tuple[Path, ...], active: set, skipped: dict) -> int:
    before = os.fstat(directory)
    active = active | {_identity(before)}
    total = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(metadata.st_mode):
                total += 1
            elif stat.S_ISDIR(metadata.st_mode):
                child = _open_directory(directory, entry.name)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise ScanError("directory_changed")
                    if _identity(opened) in active:
                        skipped["cycle"] = skipped.get("cycle", 0) + 1
                    else:
                        total += _scan(child, physical / entry.name, roots, active, skipped)
                    _same_entry(directory, entry.name, opened)
                finally:
                    os.close(child)
            elif stat.S_ISLNK(metadata.st_mode):
                try:
                    with _resolve(physical / entry.name, roots) as (child, target, resolved):
                        if stat.S_ISREG(target.st_mode):
                            total += 1
                        elif child is not None:
                            if _identity(target) in active:
                                raise SkipLink("cycle")
                            total += _scan(child, resolved, roots, active, skipped)
                    _same_entry(directory, entry.name, metadata)
                except SkipLink as error:
                    reason = str(error)
                    skipped[reason] = skipped.get(reason, 0) + 1
    after = os.fstat(directory)
    if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
        raise ScanError("directory_changed")
    return total


def count(
    root: str, path: str, skipped: dict | None = None, *,
    allowed_roots: tuple[Path, ...] | None = None,
) -> int:
    """Count logical entries; skip abnormal links inside the allowed roots."""
    if not path.strip() or "\x00" in path or len(path) > 4096:
        raise ScanError("invalid_path")
    trusted = Path(root)
    requested = Path(path)
    # 以下为安全注释COSEC：只接受引擎根内路径，禁止父级引用；不使用 resolve 后绝对重开。
    if not trusted.is_absolute() or ".." in trusted.parts:
        raise ScanError("path_forbidden")
    if ".." in requested.parts:
        raise ScanError("path_forbidden")
    roots = (trusted, trusted.parent / "openclawExt") if allowed_roots is None else allowed_roots
    # COSEC: only the composition root grants scan boundaries; never widen them.
    if not roots or any(not item.is_absolute() or item == Path("/") or ".." in item.parts for item in roots):
        raise ScanError("path_forbidden")
    requested = requested if requested.is_absolute() else trusted / requested
    if not any(requested.is_relative_to(allowed) for allowed in roots):
        raise ScanError("path_forbidden")
    if skipped is None:
        skipped = {}
    scanning = False
    try:
        with _resolve(requested, roots) as (directory, metadata, physical):
            if directory is None:
                raise ScanError("not_directory")
            scanning = True
            return _scan(directory, physical, roots, set(), skipped)
    except SkipLink as error:
        reason = str(error)
        skipped[reason] = skipped.get(reason, 0) + 1
        return 0
    except FileNotFoundError:
        raise ScanError("directory_changed" if scanning else "path_not_found") from None
    except NotADirectoryError:
        raise ScanError("directory_changed" if scanning else "not_directory") from None
    except PermissionError:
        raise ScanError("permission_denied") from None
    except OSError:
        raise ScanError("scan_failed") from None


def main() -> None:
    try:
        skipped = {}
        roots = tuple(Path(item) for item in json.loads(sys.argv[3])) if len(sys.argv) > 3 else None
        result = {"file_count": count(sys.argv[1], sys.argv[2], skipped, allowed_roots=roots)}
        if skipped:
            result["skipped_links"] = skipped
    except ScanError as error:
        result = {"error": str(error)}
    except Exception:
        # 以下为安全注释COSEC：IPC 和日志都不输出原始异常或文件系统内部路径。
        result = {"error": "scan_failed"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
