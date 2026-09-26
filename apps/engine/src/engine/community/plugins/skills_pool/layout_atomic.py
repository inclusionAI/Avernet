"""多引擎 Skills Pool 同文件系统路径原子交换原语。"""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path


def atomic_rename_if_absent(source: Path, target: Path) -> bool:
    """Atomically rename ``source`` to an absent ``target``.

    Return ``False`` only when ``target`` already exists. Unsupported or
    cross-filesystem operations stay visible as errors so callers never fall
    back to an overwrite.
    """

    if source.parent.stat().st_dev != target.parent.stat().st_dev:
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), str(target))
    libc = ctypes.CDLL(None, use_errno=True)
    source_raw = os.fsencode(source)
    target_raw = os.fsencode(target)

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
        result = renameat2(-100, source_raw, -100, target_raw, 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_raw, target_raw, 0x00000004)
    else:
        raise OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP), str(target))

    if result == 0:
        return True
    current_errno = ctypes.get_errno()
    if current_errno == errno.EEXIST:
        return False
    raise OSError(current_errno, os.strerror(current_errno), str(target))


def atomic_exchange_paths(left: Path, right: Path) -> bool:
    """以系统原生单次操作交换两个目录项，不支持时返回 ``False``。"""

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
        errno.EINVAL,
        errno.ENOSYS,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        errno.EXDEV,
    }:
        return False
    raise OSError(current_errno, os.strerror(current_errno))


__all__ = ["atomic_exchange_paths", "atomic_rename_if_absent"]
