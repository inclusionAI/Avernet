"""Corp-free boot helpers shared by the community and corp entrypoints.

Both the community/local entrypoint and the corp/prod entrypoint need the same
small pre-boot fixups. Keeping them here — in a stdlib-only, corp-free module —
lets the corp entrypoint reuse them (corp → community is the allowed dependency
direction) without the two entrypoints drifting.
"""
import os
import sys


def ensure_stdin_fd() -> None:
    """Force fd 0 to a valid, inheritable ``/dev/null``.

    Fixes "Bad File Descriptor" when the server spawns multi-worker processes:
    the ASGI server / prod runner capture ``sys.stdin.fileno()`` (fd 0) and pass
    it to spawn'd (fork+exec) workers, so fd 0 must be inheritable to survive
    ``exec()``. In container / ``nohup`` environments fd 0 may be
    non-inheritable or point to a closed pipe.
    """
    _devnull_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(_devnull_fd, 0)
    os.close(_devnull_fd)
    sys.stdin = os.fdopen(0, "r")
