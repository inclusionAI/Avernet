"""Local filesystem implementation of the existing bulk Skill symlink contract."""
from pathlib import Path
from typing import Any
from uuid import uuid4


class LocalSkillSymlinks:
    def __init__(self, roots: tuple[Path, ...], base_dir: Path) -> None:
        self.roots = roots
        self.base_dir = base_dir

    def _path(self, raw: str, *, link: bool = False) -> Path:
        path = Path(raw)
        if not raw.strip() or not path.is_absolute() or ".." in path.parts:
            raise ValueError("Skill path must be absolute without parent traversal")
        resolved = path.parent.resolve() / path.name if link else path.resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots):
            raise ValueError("Skill path is outside the configured engine roots")
        return resolved

    def sync(self, params: dict) -> dict:
        desired: dict[Path, Path] = {}
        # Validate the whole request before changing any existing links.
        for item in params.get("symlinks", []):
            source = self._path(item["source"])
            target = self._path(item["target"], link=True)
            if target in self.roots or target == source:
                raise ValueError("Skill target cannot replace a root or its source")
            if target in desired:
                raise ValueError(f"Duplicate Skill target: {target}")
            if not source.is_dir() or not (source / "SKILL.md").is_file():
                raise RuntimeError(f"Skill source is missing or unreadable: {source}")
            try:
                with (source / "SKILL.md").open("rb") as skill_file:
                    skill_file.read(1)
            except OSError as error:
                raise RuntimeError(f"Skill source is unreadable: {source}") from error
            for parent in target.parents:
                if parent.exists() and not parent.is_dir():
                    raise RuntimeError(f"Skill target parent is occupied: {parent}")
            if target.exists() and not target.is_symlink():
                raise RuntimeError(f"Skill target is occupied: {target}")
            desired[target] = source
        result: dict[str, Any] = {"total": len(desired), "created": [], "updated": [], "kept": [], "removed": []}
        for target, source in desired.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() and target.resolve() == source:
                result["kept"].append(str(target))
                continue
            key = "updated" if target.is_symlink() else "created"
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.symlink_to(source, target_is_directory=True)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            result[key].append(str(target))
        if params.get("clean_target_dir", True):
            for directory in sorted({target.parent for target in desired}):
                for entry in sorted(directory.iterdir()):
                    if entry.is_symlink() and entry not in desired:
                        entry.unlink()
                        result["removed"].append(str(entry))
        return result

    def clean(self, params: dict) -> dict:
        directories = [self._path(raw) for raw in params.get("directories", [])]
        removed: list[str] = []
        scanned = 0
        for directory in directories:
            if not directory.exists():
                continue
            if not directory.is_dir():
                raise ValueError(f"Skill cleanup path is not a directory: {directory}")
            scanned += 1
            for entry in sorted(directory.iterdir()):
                if entry.is_symlink():
                    entry.unlink()
                    removed.append(str(entry))
        return {"directories_scanned": scanned, "removed": removed}

    def sync_relative(self, params: dict) -> dict:
        base = self._path(str(self.base_dir))

        def absolute(raw: str) -> str:
            path = Path(raw)
            if not raw.strip() or path.is_absolute() or ".." in path.parts or path == Path("."):
                raise ValueError("Skill path must be a non-empty relative path without traversal")
            return str(base / path)

        mappings = [
            {"source": absolute(item["source"]), "target": absolute(item["target"])}
            for item in params.get("symlinks", [])
        ]
        desired = {self._path(item["target"], link=True) for item in mappings}
        stale = [entry for entry in sorted(base.rglob("*"))
                 if entry.is_symlink() and entry not in desired]
        result = self.sync({"symlinks": mappings, "clean_target_dir": False})
        for entry in stale:
            entry.unlink()
            result["removed"].append(str(entry))
        result["base_dir"] = str(base)
        for key in ("created", "updated", "kept", "removed"):
            result[key] = [str(Path(path).relative_to(base)) for path in result[key]]
        return result
