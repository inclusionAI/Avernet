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
    """The backend's OpenAPI narrowed to the public ``/openapi/v1`` surface.

    Both ``paths`` and ``components`` are narrowed: paths to ``/openapi/v1`` and
    components to only those transitively referenced by those paths. Keeping the
    whole app's components would drag every legacy/internal schema into the
    published artifact and make the compat gate block on purely-internal changes.
    """
    # Imported lazily: importing the app builds the whole DI container.
    from agentclaw.community.adapters.http.app import app

    spec = app.openapi()
    public_paths = {
        path: item
        for path, item in spec.get("paths", {}).items()
        if path.startswith(_PUBLIC_BASE)
    }
    out = {
        key: value for key, value in spec.items() if key not in ("paths", "components")
    }
    out["paths"] = public_paths
    components = _prune_components(spec.get("components") or {}, public_paths)
    if components:
        out["components"] = components
    return out


def _prune_components(
    components: dict[str, Any], kept_paths: dict[str, Any]
) -> dict[str, Any]:
    """Keep only components transitively referenced by *kept_paths*."""
    needed: set[str] = set()
    frontier: set[str] = set()
    _collect_refs(kept_paths, frontier)
    while frontier:
        ref = frontier.pop()
        if ref in needed:
            continue
        needed.add(ref)
        target = _resolve_ref(components, ref)
        if target is not None:
            sub: set[str] = set()
            _collect_refs(target, sub)
            frontier |= sub - needed

    pruned: dict[str, Any] = {}
    for ref in needed:
        section, name = _ref_parts(ref)
        if section is None or name is None:
            continue
        section_map = components.get(section)
        if isinstance(section_map, dict) and name in section_map:
            pruned.setdefault(section, {})[name] = section_map[name]
    return pruned


def _collect_refs(node: Any, acc: set[str]) -> None:
    # Any component-ref-shaped string counts (``$ref`` values, discriminator
    # mappings, …) — over-inclusion is safe; a missed ref would dangle.
    if isinstance(node, dict):
        for value in node.values():
            _collect_refs(value, acc)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, acc)
    elif isinstance(node, str) and node.startswith("#/components/"):
        acc.add(node)


def _ref_parts(ref: str) -> tuple[str | None, str | None]:
    parts = ref.split("/")
    if len(parts) < 4 or parts[0] != "#" or parts[1] != "components":
        return None, None
    return parts[2], "/".join(parts[3:])


def _resolve_ref(components: dict[str, Any], ref: str) -> Any:
    section, name = _ref_parts(ref)
    if section is None or name is None:
        return None
    section_map = components.get(section)
    return section_map.get(name) if isinstance(section_map, dict) else None


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
