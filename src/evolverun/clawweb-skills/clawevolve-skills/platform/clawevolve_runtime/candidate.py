"""Observe candidate changes without modifying business files or decisions."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from . import runtime


def _manifest(workspace: Path) -> dict[str, Any]:
    entries = {}
    for directory, names, files in os.walk(workspace, followlinks=False):
        for name in names + files:
            path = Path(directory) / name
            info = path.lstat()
            relative = path.relative_to(workspace).as_posix()
            if path.is_symlink():
                entries[relative] = {"link": os.readlink(path)}
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                entries[relative] = {"sha256": digest.hexdigest(), "mode": stat.S_IMODE(info.st_mode)}
    return entries


class CandidateChanges:
    """First-call snapshot per feedback round; HITL and retries retain it."""

    def __init__(self, workspace: str, target: str, snapshot: Path):
        self.workspace = Path(workspace).resolve()
        self.target = Path(target).resolve()
        self.target.relative_to(self.workspace)
        if not self.workspace.is_dir() or not self.target.is_dir():
            raise ValueError("candidate workspace and target Skill must exist")
        if snapshot.exists():
            stored = runtime._read_json(snapshot, strict=True)
            if stored.get("workspace") != str(self.workspace) or stored.get("target") != str(self.target):
                raise ValueError("candidate scope changed during the feedback round")
            self.before = stored["entries"]
        else:
            self.before = _manifest(self.workspace)
            runtime._atomic_json(snapshot, {"workspace": str(self.workspace), "target": str(self.target), "entries": self.before})

    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        after = _manifest(self.workspace)
        changed = sorted(name for name in self.before.keys() | after.keys() if self.before.get(name) != after.get(name))
        actual = []
        for name in changed:
            path = self.workspace / name
            try:
                relative = path.relative_to(self.target).as_posix()
                path.resolve().relative_to(self.target)
            except ValueError as exc:
                raise ValueError(f"Hardening changed a file outside the target Skill: {name}") from exc
            actual.append(relative)
        if result["changed"] != bool(actual):
            raise ValueError("Hardening changed flag does not match the observed candidate changes")
        if not set(result.get("changed_files", [])).issubset(actual):
            raise ValueError("Hardening changed_files includes a file that did not change")
        return {**result, "changed_files": actual}
