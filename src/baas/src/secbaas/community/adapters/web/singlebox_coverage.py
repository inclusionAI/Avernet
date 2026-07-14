"""Singlebox coverage hooks for the BaaS HTTP adapter layer."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

_LOCK = threading.Lock()


def _coverage_dir() -> Path | None:
    root = os.environ.get("SINGLEBOX_COVERAGE_DIR")
    if not root:
        return None
    return Path(root) / "baas"


def _append_jsonl(filename: str, payload: dict[str, Any]) -> None:
    if os.environ.get("SINGLEBOX_COVERAGE") != "1":
        return
    coverage_dir = _coverage_dir()
    if coverage_dir is None:
        return
    coverage_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        **payload,
    }
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with _LOCK:
        with (coverage_dir / filename).open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def install_singlebox_coverage_middleware(app: FastAPI) -> None:
    """Record concrete FastAPI route hits while singlebox coverage is enabled."""

    @app.middleware("http")
    async def _singlebox_coverage_router_hit_middleware(
        request: Request,
        call_next,
    ):
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", None) or request.url.path
        method = request.method.upper()
        _append_jsonl(
            "router_hits.jsonl",
            {
                "key": f"{method} {route_path}",
                "method": method,
                "path": request.url.path,
                "route_path": route_path,
                "status_code": response.status_code,
            },
        )
        return response
