"""Generate the served OpenAPI doc from a backend's published description.

The doc is the backend's own description, narrowed to the public namespace and
annotated with each operation's auth requirement. Because paths forward
verbatim, no path rewriting is needed: the description's paths are the
client-facing paths. Pure logic (Rule 7) — no web framework.
"""

from __future__ import annotations

import copy
from typing import Any

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import Delegation, StrategyParams

_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
)
_DEFAULT_BASE_PATH = "/openapi/v1"


def generate_openapi(
    description: dict[str, Any],
    rules: RouteSecurity,
    base_path: str = _DEFAULT_BASE_PATH,
) -> dict[str, Any]:
    """Return the served doc: public-namespace paths + auth metadata + used schemas."""
    doc = {k: v for k, v in description.items() if k not in ("paths", "components")}
    raw_paths = description.get("paths") or {}
    kept = {
        path: _with_security(path, item, rules)
        for path, item in raw_paths.items()
        if _in_namespace(path, base_path)
    }
    doc["paths"] = kept
    components = _prune_components(description.get("components") or {}, kept)
    if components:
        doc["components"] = components
    return doc


def _in_namespace(path: str, base_path: str) -> bool:
    return path == base_path or path.startswith(base_path + "/")


def _with_security(
    path: str, item: dict[str, Any], rules: RouteSecurity
) -> dict[str, Any]:
    """Copy a path item, attaching ``x-avernet-security`` to each operation."""
    new_item = dict(item)
    for method, operation in item.items():
        if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
            continue
        requirement = rules.resolve(method.upper(), path)
        if requirement is None:
            continue
        new_op = dict(operation)
        new_op["x-avernet-security"] = [
            {name: _params_to_dict(params) for name, params in alternative.items()}
            for alternative in requirement
        ]
        new_item[method] = new_op
    return new_item


def _params_to_dict(params: StrategyParams) -> dict[str, Any]:
    """Serialize strategy params, omitting defaults (empty scopes, optional)."""
    out: dict[str, Any] = {}
    if params.scopes:
        out["scopes"] = sorted(params.scopes)
    if params.delegation is not Delegation.OPTIONAL:
        out["delegation"] = params.delegation.value
    return out


# ── component pruning ────────────────────────────────────────────────────────


def _prune_components(
    components: dict[str, Any], kept_paths: dict[str, Any]
) -> dict[str, Any]:
    """Keep only components transitively referenced by the kept paths."""
    needed: set[str] = set()
    frontier: set[str] = set()
    _collect_refs(kept_paths, frontier)
    while frontier:
        ref = frontier.pop()
        if ref in needed:
            continue
        needed.add(ref)
        target = _resolve_ref(components, ref)
        if target is None:
            continue
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
            pruned.setdefault(section, {})[name] = copy.deepcopy(section_map[name])
    return pruned


def _collect_refs(node: Any, acc: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                key == "$ref"
                and isinstance(value, str)
                and value.startswith("#/components/")
            ):
                acc.add(value)
            else:
                _collect_refs(value, acc)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, acc)


def _ref_parts(ref: str) -> tuple[str | None, str | None]:
    # "#/components/<section>/<name>"
    parts = ref.split("/")
    if len(parts) < 4 or parts[0] != "#" or parts[1] != "components":
        return None, None
    return parts[2], "/".join(parts[3:])


def _resolve_ref(components: dict[str, Any], ref: str) -> Any:
    section, name = _ref_parts(ref)
    if section is None or name is None:
        return None
    section_map = components.get(section)
    if isinstance(section_map, dict):
        return section_map.get(name)
    return None
