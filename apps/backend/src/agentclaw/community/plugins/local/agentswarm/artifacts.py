from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from agentclaw.community.plugin_api.agentswarm.artifact import ArtifactStore

logger = logging.getLogger(__name__)

_TEMP_PREFIX = ".agentswarm-tmp-"

# Upper bound on a single stored artifact (8 MiB). Subagent results spill
# here once past the inline threshold, and an engine reply is untrusted in
# size — a ``bot``-kind subagent can return an arbitrarily large
# payload. Without a cap, a single run can exhaust the disk backing the
# artifact root (default ``/tmp/agentswarm/artifacts``). Oversize writes
# raise ValueError before any filesystem mutation; the runtime treats
# spilling as best-effort and catches the raise, keeping the result inline
# (see runtime/pool/impl.py ``_mk_result``).
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def _resolve_within(root: Path, key: str) -> Path:
    parts = key.split("/")
    resolved = root.joinpath(*parts).resolve()
    # Containment is a path-component relation, not a string prefix:
    # ``root=/x/art`` and a sibling ``/x/art-evil`` pass a startswith
    # check but not is_relative_to.
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"key escapes root: {key}")
    return resolved


def _atomic_write(target: Path, write) -> None:
    """Stage content in a same-directory temp file, then os.replace.

    os.replace is the single visibility point onto ``target``: readers
    see the old content or the new content, never a partial file, and a
    crash mid-write leaves the old file intact rather than corrupt.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                     prefix=_TEMP_PREFIX)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            write(fh)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


class FilesystemArtifactStore:

    def __init__(self, root: str = "/tmp/agentswarm/artifacts") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, key: str, content: bytes | str) -> bool:
        """Store ``content`` under ``key`` atomically; True on success.

        Raises ValueError — before any filesystem mutation — when the
        payload (encoded to UTF-8 for ``str``) exceeds
        ``_MAX_ARTIFACT_BYTES``, or when ``key`` escapes the root.
        """
        target = self._resolve(key)
        payload = content.encode("utf-8") if isinstance(content, str) else content
        if len(payload) > _MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"artifact exceeds {_MAX_ARTIFACT_BYTES} bytes: {len(payload)}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, lambda fh: fh.write(payload))
        return True

    def put_file(self, key: str, local_path: str) -> bool:
        """Copy the file at ``local_path`` under ``key`` atomically.

        True on success; False when the source does not exist. Raises
        ValueError — checked via ``stat`` before any copying, leaving no
        target or temp residue — when the source exceeds
        ``_MAX_ARTIFACT_BYTES``, or when ``key`` escapes the root.
        """
        source = Path(local_path)
        if not source.exists():
            return False
        target = self._resolve(key)
        size = source.stat().st_size
        if size > _MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"artifact exceeds {_MAX_ARTIFACT_BYTES} bytes: {size}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        def _copy_into(fh) -> None:
            with open(source, "rb") as src_fh:
                shutil.copyfileobj(src_fh, fh)

        _atomic_write(target, _copy_into)
        return True

    def delete(self, key: str) -> bool:
        target = self._resolve(key)
        if target.exists():
            target.unlink()
            return True
        return False

    def get(self, key: str) -> bytes | None:
        target = self._resolve(key)
        if not target.exists():
            return None
        return target.read_bytes()

    def retention_scanner(self) -> "FilesystemRetentionScanner":
        return FilesystemRetentionScanner(self)

    def _resolve(self, key: str) -> Path:
        return _resolve_within(self._root, key)


class FilesystemRetentionScanner:
    """RetentionScanner over one store's root — store-level only, no DB (12.9).

    Keys are derived by walking the root itself, so every listed key is
    root-relative by construction; ``key_mtime`` still routes through the same
    escape guard as the store so a malformed key raises instead of stat-ing
    outside the root. Broken symlinks are listed but their mtime lookup fails
    (getmtime follows the link) — the sweeper treats that as a per-key error
    and continues, never deleting on unreadable age.
    """

    def __init__(self, store: FilesystemArtifactStore) -> None:
        self._store = store

    def list_keys(self) -> list[str]:
        root = self._store.root
        keys = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() or path.is_symlink()
        ]
        return sorted(keys)

    def key_mtime(self, key: str) -> float:
        return os.path.getmtime(self._resolve(key))

    def _resolve(self, key: str) -> Path:
        return _resolve_within(self._store.root, key)