#!/usr/bin/env python3
"""One-time and change-aware OpenClaw adaptation for ClawEvolve."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
RULES: dict[str, dict[str, Any]] = {
    "agent-guard": {
        "enabled": False,
        "hooks": {"allowConversationAccess": True},
    },
    "clawmind": {
        "enabled": True,
        "hooks": {"allowConversationAccess": True},
        "config": {
            "api": {"enabled": False},
            "flowControl": {"enabled": False},
            "contextCompression": {"enabled": False},
        },
    },
}


def fingerprint(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    info = path.stat()
    return {"mtime_ns": info.st_mtime_ns, "size": info.st_size}


def read_marker(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def marker_is_current(marker_path: Path, config_path: Path) -> bool:
    marker = read_marker(marker_path)
    return bool(
        marker
        and marker.get("schema_version") == SCHEMA_VERSION
        and marker.get("status") == "ready"
        and marker.get("config_fingerprint") == fingerprint(config_path)
    )


def merge_required(target: dict[str, Any], required: dict[str, Any]) -> bool:
    changed = False
    for key, value in required.items():
        if isinstance(value, dict):
            current = target.get(key)
            if not isinstance(current, dict):
                target[key] = {}
                current = target[key]
                changed = True
            changed = merge_required(current, value) or changed
        elif key not in target or target[key] != value:
            target[key] = value
            changed = True
    return changed


def atomic_json_write(path: Path, value: dict[str, Any], source_stat: os.stat_result | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if source_stat:
            os.chmod(temporary, stat.S_IMODE(source_stat.st_mode))
            try:
                os.chown(temporary, source_stat.st_uid, source_stat.st_gid)
            except PermissionError:
                pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ready_marker(config_path: Path, engine: str, plugins: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "engine": engine,
        "checked_plugins": plugins,
        "config_fingerprint": fingerprint(config_path),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def check_and_adapt(config_path: Path, marker_path: Path) -> dict[str, Any]:
    if marker_is_current(marker_path, config_path):
        return {"ok": True, "status": "cached"}
    if not config_path.exists():
        atomic_json_write(marker_path, ready_marker(config_path, "other", []))
        return {"ok": True, "status": "unchanged", "engine": "other"}
    if not config_path.is_file():
        raise ValueError(f"OpenClaw config is not a regular file: {config_path}")

    source_stat = config_path.stat()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("OpenClaw config root must be a JSON object")
    plugins = config.get("plugins")
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    if entries is not None and not isinstance(entries, dict):
        raise ValueError("plugins.entries must be a JSON object")

    changed = False
    checked: list[str] = []
    if isinstance(entries, dict):
        for name, rule in RULES.items():
            if name not in entries:
                continue
            if not isinstance(entries[name], dict):
                raise ValueError(f"plugins.entries.{name} must be a JSON object")
            checked.append(name)
            changed = merge_required(entries[name], rule) or changed

    if not changed:
        atomic_json_write(marker_path, ready_marker(config_path, "openclaw", checked))
        return {"ok": True, "status": "unchanged", "engine": "openclaw", "checked_plugins": checked}

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = config_path.with_name(f"{config_path.name}.clawevolve-backup-{timestamp}-{os.getpid()}")
    shutil.copy2(config_path, backup)
    atomic_json_write(config_path, config, source_stat)
    return {
        "ok": True,
        "status": "changed",
        "engine": "openclaw",
        "checked_plugins": checked,
        "backup_path": str(backup),
    }


def restore(config_path: Path, backup_path: Path) -> None:
    if not backup_path.is_file():
        raise ValueError(f"backup is not a regular file: {backup_path}")
    temporary = config_path.with_name(f".{config_path.name}.restore.{os.getpid()}")
    try:
        shutil.copy2(backup_path, temporary)
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--mark-ready", action="store_true")
    args = parser.parse_args()
    try:
        if args.restore:
            restore(args.config, args.restore)
            result = {"ok": True, "status": "restored"}
        elif args.mark_ready:
            if not args.marker:
                raise ValueError("--marker is required with --mark-ready")
            config = json.loads(args.config.read_text(encoding="utf-8"))
            entries = config.get("plugins", {}).get("entries", {})
            checked = [name for name in RULES if name in entries]
            atomic_json_write(args.marker, ready_marker(args.config, "openclaw", checked))
            result = {"ok": True, "status": "ready"}
        else:
            if not args.marker:
                raise ValueError("--marker is required")
            result = check_and_adapt(args.config, args.marker)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
