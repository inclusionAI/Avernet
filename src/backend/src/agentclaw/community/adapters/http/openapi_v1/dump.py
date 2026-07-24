"""Dump the public ``/openapi/v1`` description as the gateway's pinned artifact.

Run in CI on release: it produces the backend's *published description* (the
public surface only), which the gateway consumes to generate its served doc.
Deterministic (sorted keys) so drift/compat diffs are stable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PUBLIC_BASE = "/openapi/v1"


def build_public_openapi() -> dict[str, Any]:
    """The backend's OpenAPI narrowed to the public ``/openapi/v1`` paths."""
    # Imported lazily: importing the app builds the whole DI container.
    from agentclaw.community.adapters.http.app import app

    spec = app.openapi()
    public_paths = {
        path: item
        for path, item in spec.get("paths", {}).items()
        if path.startswith(_PUBLIC_BASE)
    }
    out = {key: value for key, value in spec.items() if key != "paths"}
    out["paths"] = public_paths
    return out


def dump_openapi(target: str | Path) -> dict[str, Any]:
    """Write the public description to *target* (deterministic JSON)."""
    spec = build_public_openapi()
    Path(target).write_text(
        json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return spec


if __name__ == "__main__":  # pragma: no cover - CLI entry for CI
    import sys

    dest = sys.argv[1] if len(sys.argv) > 1 else "bots.openapi.json"
    dump_openapi(dest)
    print(f"wrote public OpenAPI to {dest}")
