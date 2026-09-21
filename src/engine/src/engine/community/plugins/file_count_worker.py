"""Fixed isolated worker: count directory entries without following links.

This module is also directly executable; only stdlib imports are needed.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat
import sys


class ScanError(Exception):
    """Safe worker error identifier."""


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


def _scan(directory: int) -> int:
    before = os.fstat(directory)
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
                    total += _scan(child)
                    _same_entry(directory, entry.name, opened)
                finally:
                    os.close(child)
    after = os.fstat(directory)
    if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
        raise ScanError("directory_changed")
    return total


def count(root: str, path: str) -> int:
    """Open every root/target component without links and retain its ancestry."""
    if not path.strip() or "\x00" in path or len(path) > 4096:
        raise ScanError("invalid_path")
    trusted = Path(root)
    requested = Path(path)
    # 以下为安全注释COSEC：只接受引擎根内路径，禁止父级引用；不使用 resolve 后绝对重开。
    if not trusted.is_absolute() or ".." in trusted.parts:
        raise ScanError("path_forbidden")
    if ".." in requested.parts:
        raise ScanError("path_forbidden")
    if requested.is_absolute():
        try:
            requested = requested.relative_to(trusted)
        except ValueError:
            raise ScanError("path_forbidden") from None
    descriptors = [os.open("/", os.O_RDONLY | os.O_DIRECTORY)]
    ancestors = []
    scanning = False
    try:
        for component in (*trusted.parts[1:], *requested.parts):
            parent = descriptors[-1]
            child = _open_directory(parent, component)
            descriptors.append(child)
            ancestors.append((parent, component, os.fstat(child)))
        scanning = True
        total = _scan(descriptors[-1])
        for parent, component, metadata in ancestors:
            _same_entry(parent, component, metadata)
        return total
    except FileNotFoundError:
        raise ScanError("directory_changed" if scanning else "path_not_found") from None
    except NotADirectoryError:
        raise ScanError("directory_changed" if scanning else "not_directory") from None
    except PermissionError:
        raise ScanError("permission_denied") from None
    except OSError:
        raise ScanError("scan_failed") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def main() -> None:
    try:
        result = {"file_count": count(sys.argv[1], sys.argv[2])}
    except ScanError as error:
        result = {"error": str(error)}
    except Exception:
        # 以下为安全注释COSEC：IPC 和日志都不输出原始异常或文件系统内部路径。
        result = {"error": "scan_failed"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
