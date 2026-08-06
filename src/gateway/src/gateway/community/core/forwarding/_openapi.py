"""Generate the served OpenAPI doc from a backend's published description.

The doc is the backend's own description, narrowed to the public namespace and
annotated with each operation's auth requirement. Path rewrites are
reverse-applied so the served doc shows gateway-facing paths that clients
actually use. Pure logic (Rule 7) — no web framework.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding._domains import PathRewrite

_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
)
_DEFAULT_BASE_PATH = "/openapi/v1"


def build_served_openapi(
    domains: Iterable[str],
    describe: Callable[[str], dict[str, Any]],
    rules: RouteSecurity,
    *,
    title: str,
    version: str,
    description: str = "",
    base_path: str = _DEFAULT_BASE_PATH,
    rewrites: Mapping[str, PathRewrite | None] | None = None,
) -> dict[str, Any]:
    """Merge every domain's generated doc into the single served document.

    ``describe(domain)`` returns that domain's latest published description (from
    the schema catalog). Domains with no description yet contribute nothing.

    Each domain filters paths using ``{base_path}/{domain_name}``, so only paths
    beneath that domain's prefix are included. When a domain has a path rewrite,
    upstream paths are reverse-mapped to gateway-facing paths.
    """
    paths: dict[str, Any] = {}
    components: dict[str, Any] = {}
    tags: list[dict[str, Any]] = []
    tag_names: set[str] = set()
    for domain in domains:
        domain_prefix = f"{base_path.rstrip('/')}/{domain}"
        rewrite = (rewrites or {}).get(domain)
        doc = generate_openapi(describe(domain), rules, domain_prefix, rewrite=rewrite)
        paths.update(doc.get("paths", {}))
        for section, items in doc.get("components", {}).items():
            components.setdefault(section, {}).update(items)
        for tag in doc.get("tags", []):
            if not isinstance(tag, dict):
                continue
            name = tag.get("name")
            if not isinstance(name, str) or name in tag_names:
                continue
            tag_names.add(name)
            tags.append(copy.deepcopy(tag))
    info: dict[str, Any] = {"title": title, "version": version}
    if description:
        info["description"] = description
    served: dict[str, Any] = {"openapi": "3.1.0", "info": info, "paths": paths}
    if components:
        served["components"] = components
    if tags:
        served["tags"] = tags
    return served


def generate_openapi(
    description: dict[str, Any],
    rules: RouteSecurity,
    base_path: str = _DEFAULT_BASE_PATH,
    *,
    rewrite: PathRewrite | None = None,
) -> dict[str, Any]:
    """Return the served doc: public-namespace paths + auth metadata + used schemas.

    When *rewrite* is given, upstream-internal paths are reverse-mapped to
    gateway-facing paths before filtering and emission.
    """
    doc = {k: v for k, v in description.items() if k not in ("paths", "components")}
    raw_paths = description.get("paths") or {}
    kept: dict[str, Any] = {}
    for path, item in raw_paths.items():
        lookup_path = path
        if rewrite is not None:
            # The upstream publishes its own internal paths; filter against the
            # reversed (gateway-facing) path, but resolve security against the
            # original path since RouteSecurity is keyed by gateway paths.
            lookup_path = rewrite.reverse(path)
        if _in_namespace(lookup_path, base_path):
            kept[lookup_path] = _with_security(path, item, rules)
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
        new_op["x-avernet-security"] = {
            identity.value: presence.value for identity, presence in requirement.items()
        }
        new_item[method] = new_op
    return new_item


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
    # Any string shaped like a component ref counts — not only `$ref` values but
    # also `discriminator.mapping` values and other ref-bearing fields. Over-
    # inclusion is harmless (an unused schema is kept); a missed ref would leave
    # the served doc with a dangling reference.
    if isinstance(node, dict):
        for value in node.values():
            _collect_refs(value, acc)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, acc)
    elif isinstance(node, str) and node.startswith("#/components/"):
        acc.add(node)


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
