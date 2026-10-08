"""HTTP route-hit recorder for singlebox coverage mode.

Plugin evidence is derived offline from coverage.py artifacts; runtime business
objects do not emit coverage events.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("SINGLEBOX_COVERAGE") == "1"


def _backend_hit_dir() -> Path | None:
    root = os.environ.get("SINGLEBOX_COVERAGE_DIR")
    if not root:
        return None
    return Path(root) / "backend"


def _append_jsonl(filename: str, payload: dict[str, Any]) -> None:
    if not enabled():
        return
    hit_dir = _backend_hit_dir()
    if hit_dir is None:
        return
    hit_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        **payload,
    }
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with _LOCK:
        with (hit_dir / filename).open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def record_router_hit(*, method: str, route_path: str, path: str, status_code: int) -> None:
    key = f"{method.upper()} {route_path}"
    _append_jsonl(
        "router_hits.jsonl",
        {
            "key": key,
            "method": method.upper(),
            "route_path": route_path,
            "path": path,
            "status_code": status_code,
        },
    )
