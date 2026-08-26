"""Generate the served OpenAPI doc from a backend's published description.

The doc is the backend's own description, narrowed to the public namespace and
annotated with each operation's auth requirement. Path rewrites are
reverse-applied so the served doc shows gateway-facing paths that clients
actually use. Pure logic (Rule 7) — no web framework.

**The gateway is the only writer of auth on the served document.** An upstream
may publish its own ``x-avernet-security`` — bcs authors one per operation in
its api-contracts, the backend authors none — and those blocks are stripped
here before this module stamps its own. Two producers of one fact is one
producer too many: the gateway is what actually reads credentials and refuses
requests, so what it enforces is what the document says, and an upstream block
can no longer disagree with the route-security table or survive on a path the
table happens not to match.

Auth is stamped in two forms. ``security`` + ``components.securitySchemes`` is
the standard one, and the only one a third-party client or a code generator can
act on: it names the credential to present. ``x-avernet-security`` stays beside
it as the internal record of which identities the gateway resolves into the
signed principal — the same fact at a different altitude, now unambiguously
derived rather than authored.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from itertools import combinations
from types import MappingProxyType
from typing import Any

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.forwarding._domains import PathRewrite
from gateway.community.spi.authn import (
    CredentialLocation,
    CredentialSpec,
    Presence,
    PrincipalType,
)

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
    mount_prefixes: Mapping[str, str] | None = None,
    credentials: Mapping[PrincipalType, CredentialSpec] = MappingProxyType({}),
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
        # Default to /openapi/v1/<name>; a caller that passes mount_prefixes can
        # name a matched-child domain whose real prefix differs from that, so its
        # paths survive the namespace filter below.
        domain_prefix = (mount_prefixes or {}).get(
            domain, f"{base_path.rstrip('/')}/{domain}"
        )
        rewrite = (rewrites or {}).get(domain)
        doc = generate_openapi(
            describe(domain),
            rules,
            domain_prefix,
            rewrite=rewrite,
            credentials=credentials,
        )
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
    schemes = _security_schemes(credentials)
    if schemes:
        components.setdefault("securitySchemes", {}).update(schemes)
    info: dict[str, Any] = {"title": title, "version": version}
    if description:
        info["description"] = description
    served: dict[str, Any] = {"openapi": "3.1.0", "info": info, "paths": paths}
    if components:
        served["components"] = components
    if tags:
        served["tags"] = tags
    return served


def build_combined_openapi(
    domains: Iterable[str],
    describe: Callable[[str], dict[str, Any]],
    *,
    title: str,
    version: str,
    description: str = "",
) -> dict[str, Any]:
    """Merge already-scoped OpenAPI descriptions into one served document.

    Unlike :func:`build_served_openapi`, this does not apply the public
    ``/openapi/v1`` namespace filter. It is used for documentation surfaces such
    as internal APIs where each configured schema is already scoped to the
    address space it documents.
    """
    paths: dict[str, Any] = {}
    components: dict[str, Any] = {}
    tags: list[dict[str, Any]] = []
    tag_names: set[str] = set()
    for domain in domains:
        doc = describe(domain)
        paths.update(copy.deepcopy(doc.get("paths", {})))
        for section, items in doc.get("components", {}).items():
            components.setdefault(section, {}).update(copy.deepcopy(items))
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
    credentials: Mapping[PrincipalType, CredentialSpec] = MappingProxyType({}),
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
            kept[lookup_path] = _with_security(path, item, rules, credentials)
    doc["paths"] = kept
    components = _prune_components(description.get("components") or {}, kept)
    if components:
        doc["components"] = components
    return doc


def _in_namespace(path: str, base_path: str) -> bool:
    return path == base_path or path.startswith(base_path + "/")


def _with_security(
    path: str,
    item: dict[str, Any],
    rules: RouteSecurity,
    credentials: Mapping[PrincipalType, CredentialSpec],
) -> dict[str, Any]:
    """Copy a path item, replacing each operation's auth with the gateway's own.

    Any ``x-avernet-security`` the upstream published is dropped first, so an
    operation the route-security table does not match carries no auth claim at
    all rather than an upstream's stale one.
    """
    new_item = dict(item)
    for method, operation in item.items():
        if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
            continue
        new_op = {k: v for k, v in operation.items() if k != "x-avernet-security"}
        requirement = rules.resolve(method.upper(), path)
        if requirement is not None:
            new_op["x-avernet-security"] = {
                identity.value: presence.value
                for identity, presence in requirement.items()
            }
            if _is_describable(requirement, credentials):
                new_op["security"] = _security_alternatives(requirement, credentials)
        new_item[method] = new_op
    return new_item


# ── security (standard OpenAPI) ──────────────────────────────────────────────


def _is_describable(
    requirement: Mapping[PrincipalType, Presence],
    credentials: Mapping[PrincipalType, CredentialSpec],
) -> bool:
    """Whether every identity the route wants has a credential to name.

    An identity with no registered strategy cannot be presented by any client,
    so the honest rendering is to omit ``security`` for that operation rather
    than publish a scheme nobody can satisfy. Omission and ``security: []`` are
    different claims — the latter says the operation needs no credential — so
    the two cases must not collapse into one another.
    """
    return all(identity in credentials for identity in requirement)


def _security_alternatives(
    requirement: Mapping[PrincipalType, Presence],
    credentials: Mapping[PrincipalType, CredentialSpec],
) -> list[dict[str, list[str]]]:
    """Render one route requirement as OpenAPI ``security`` alternatives.

    OpenAPI reads the list as OR and each entry as AND, which is exactly the
    shape the requirement needs: every ``required`` identity in each entry, and
    one entry per subset of the ``optional`` ones.

    The empty subset is dropped when nothing is required, making the rendering
    "at least one of". That is deliberate and matches what the deployment
    enforces: ``configs/application.yaml`` notes that the requirement table
    cannot express "user or app" and leans on an upstream guard to refuse a
    caller presenting neither. OpenAPI can express it, so the served document
    states the real rule rather than the table's approximation of it.

    An empty requirement is a genuinely public operation and renders as ``[]``.
    Callers must have checked :func:`_is_describable` first.
    """
    required = sorted(
        (i for i, p in requirement.items() if p is Presence.REQUIRED),
        key=lambda i: credentials[i].scheme_name,
    )
    optional = sorted(
        (i for i, p in requirement.items() if p is Presence.OPTIONAL),
        key=lambda i: credentials[i].scheme_name,
    )
    alternatives: list[dict[str, list[str]]] = []
    for size in range(len(optional) + 1):
        for subset in combinations(optional, size):
            identities = [*required, *subset]
            if not identities:
                continue  # nothing required and nothing chosen → "at least one of"
            alternatives.append({credentials[i].scheme_name: [] for i in identities})
    return alternatives


def _security_schemes(
    credentials: Mapping[PrincipalType, CredentialSpec],
) -> dict[str, Any]:
    """Render the registered credentials as ``components.securitySchemes``."""
    schemes: dict[str, Any] = {}
    for spec in sorted(credentials.values(), key=lambda s: s.scheme_name):
        if spec.location is CredentialLocation.BEARER:
            scheme: dict[str, Any] = {"type": "http", "scheme": "bearer"}
        else:
            scheme = {
                "type": "apiKey",
                "in": spec.location.value,
                "name": spec.name,
            }
        if spec.description:
            scheme["description"] = spec.description
        schemes[spec.scheme_name] = scheme
    return schemes


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
