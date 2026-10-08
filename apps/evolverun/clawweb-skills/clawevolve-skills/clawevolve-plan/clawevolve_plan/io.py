from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest(paths: Iterable[Path]) -> Path | None:
    existing = [p for p in paths if p.exists()]
    return max(existing, key=_safe_mtime) if existing else None


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_plan_source(run_dir: Path) -> Path | None:
    if run_dir.is_file():
        if run_dir.name != "plan-source.json":
            raise ValueError(f"Expected plan-source.json, got {run_dir}")
        return run_dir
    candidates = sorted(
        path
        for path in run_dir.glob("**/plan-source.json")
        if path.is_file()
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        rendered = ", ".join(str(path) for path in candidates[:8])
        suffix = f" (+{len(candidates) - 8} more)" if len(candidates) > 8 else ""
        raise ValueError(
            "Multiple Diagnose Plan Sources were found; pass --run-dir pointing to "
            f"one concrete Diagnose run directory instead of selecting by mtime: {rendered}{suffix}"
        )
    return candidates[0]


def read_discovery_notes(value: str) -> str:
    if not value:
        return ""
    path = _existing_file_path(value)
    return path.read_text(encoding="utf-8", errors="replace") if path else value


def _existing_file_path(value: str) -> Path | None:
    try:
        path = Path(value)
        return path if path.exists() and path.is_file() else None
    except OSError:
        return None


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def write_named_outputs(
    out_dir: Path, name: str, data: dict[str, Any], markdown: str
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    atomic_write_json(json_path, data)
    atomic_write_text(md_path, markdown)
    return md_path, json_path
