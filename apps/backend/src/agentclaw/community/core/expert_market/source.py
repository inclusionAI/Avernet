"""Read a single complete content revision, without following symlinks."""

from __future__ import annotations

import subprocess
import os
from pathlib import Path, PurePosixPath
from typing import Protocol


class ExpertContentSource(Protocol):
    def paths(self) -> tuple[str, ...]: ...
    def read(self, path: str) -> bytes: ...


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(p in ("", ".", "..") for p in value.split("/"))
    ):
        raise ValueError("source path must be relative and contained in the repository")
    return str(path)


class DirectoryExpertSource:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def paths(self) -> tuple[str, ...]:
        agents = self.root / "agents"
        if not agents.is_dir() or agents.is_symlink():
            raise ValueError("complete agents directory is unavailable")
        # Materialize the full enumeration before any database mutation.
        found = []

        def fail(error):
            raise error

        for directory, dirs, files in os.walk(
            self.root, followlinks=False, onerror=fail
        ):
            dirs[:] = [name for name in dirs if name != ".git"]
            for name in files + [
                name for name in dirs if (Path(directory) / name).is_symlink()
            ]:
                found.append((Path(directory) / name).relative_to(self.root).as_posix())
        return tuple(found)

    def read(self, path: str) -> bytes:
        normalized = safe_path(path)
        current = self.root
        for part in PurePosixPath(normalized).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("symlinks are not permitted in expert content")
        if current.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("expert file exceeds 10 MiB")
        return current.read_bytes()


class GitExpertSource:
    """Read immutable Git objects, including same-commit shared files."""

    def __init__(self, bare_repo: Path, commit: str):
        self.root = bare_repo
        self.commit = commit
        self._modes: dict[str, str] = {}

    def _git(self, *args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True
        ).stdout

    def paths(self) -> tuple[str, ...]:
        if self._git("cat-file", "-t", self.commit).strip() != b"commit":
            raise ValueError("expert revision must identify a Git commit")
        tree = self._git("ls-tree", "-rz", "-r", self.commit)
        for record in tree.split(b"\0"):
            if not record:
                continue
            metadata, path = record.split(b"\t", 1)
            self._modes[path.decode("utf-8", errors="surrogateescape")] = metadata.split()[0].decode()
        # An absent tree is different from a successfully enumerated empty tree.
        self._git("rev-parse", self.commit + ":agents")
        return tuple(self._modes)

    def read(self, path: str) -> bytes:
        normalized = safe_path(path)
        if self._modes.get(normalized) not in ("100644", "100755"):
            raise ValueError("expert source must be a regular same-commit file")
        data = self._git("show", self.commit + ":" + normalized)
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("expert file exceeds 10 MiB")
        return data
