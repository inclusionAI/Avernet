"""Publish one generated upstream operation without unrelated schema drift.

Use this only when a full upstream dump cannot pass the compatibility gate due
to independently tracked changes. The selected operation and every referenced
component are copied from that dump; existing operations/components are never
replaced. The resulting artifact still passes the standard compatibility gate.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from gate_and_publish_openapi import gate

_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
)


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set().union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    if isinstance(node, str) and node.startswith("#/components/"):
        return {node}
    return set()


def _component(candidate: dict[str, Any], ref: str) -> tuple[str, str, Any]:
    parts = ref.split("/")
    if len(parts) != 4 or parts[:2] != ["#", "components"]:
        raise ValueError(f"unsupported component reference: {ref}")
    section, name = parts[2:]
    item = (candidate.get("components") or {}).get(section, {}).get(name)
    if item is None:
        raise ValueError(f"missing component in upstream dump: {ref}")
    return section, name, item


def additive_document(
    published: dict[str, Any], candidate: dict[str, Any], method: str, path: str
) -> dict[str, Any]:
    """Copy exactly one new path and its transitive refs from a generated dump."""
    method = method.lower()
    if method not in _METHODS:
        raise ValueError(f"unsupported HTTP method: {method}")
    if published.get("openapi") != candidate.get("openapi"):
        raise ValueError("upstream OpenAPI version differs from the published artifact")
    source = (candidate.get("paths") or {}).get(path)
    if not isinstance(source, dict) or method not in source:
        raise ValueError(f"upstream dump has no {method.upper()} {path}")
    other_methods = _METHODS.intersection(source) - {method}
    if other_methods:
        raise ValueError(
            f"upstream path contains other methods: {sorted(other_methods)}"
        )

    # JSON round-trip gives a detached copy and preserves the dump's JSON-only
    # contract. Existing entries must stay byte-for-byte equivalent as data.
    merged = json.loads(json.dumps(published))
    existing = merged.setdefault("paths", {}).get(path)
    if existing is not None and existing != source:
        raise ValueError(f"published path differs from upstream dump: {path}")
    merged["paths"][path] = source

    seen: set[str] = set()
    frontier = _refs(source)
    while frontier:
        ref = frontier.pop()
        if ref in seen:
            continue
        seen.add(ref)
        section, name, item = _component(candidate, ref)
        target = merged.setdefault("components", {}).setdefault(section, {})
        if name in target and target[name] != item:
            raise ValueError(f"published component differs from upstream dump: {ref}")
        target[name] = item
        frontier.update(_refs(item) - seen)

    return merged


def publish(published: Path, candidate: Path, method: str, path: str) -> bool:
    """Publish the generated additive delta after the regular compat gate."""
    if not published.is_file():
        raise ValueError(f"published artifact does not exist: {published}")
    old = json.loads(published.read_text(encoding="utf-8"))
    new = json.loads(candidate.read_text(encoding="utf-8"))
    merged = additive_document(old, new, method, path)
    if merged == old:
        return False

    fd, name = tempfile.mkstemp(
        prefix=".openapi-additive-", suffix=".json", dir=published.parent
    )
    generated = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            # Preserve the checked-in artifact's ordering so the review diff is
            # limited to the selected path and newly referenced components.
            json.dump(merged, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        gate(published, generated, allow_breaking=False)
        os.replace(generated, published)
    finally:
        generated.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("published", type=Path)
    parser.add_argument("candidate", type=Path, help="fresh upstream dump")
    parser.add_argument("method", help="HTTP method to publish")
    parser.add_argument("path", help="exact upstream OpenAPI path to publish")
    args = parser.parse_args(argv)
    try:
        changed = publish(args.published, args.candidate, args.method, args.path)
    except ValueError as exc:
        parser.error(str(exc))
    print("published additive operation" if changed else "operation already published")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
