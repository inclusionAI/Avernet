"""Namespace invariant: ``/openapi/v1`` holds exactly the declared domains.

The gateway forwards ``/openapi/v1/*`` transparently, so nothing internal may
live under that prefix — every public route must sit under a prefix the
gateway's shipped configuration routes and secures. Those prefixes are the
declared upstream domains (``upstreams.domains`` and ``route_security`` in
``src/gateway/configs/application.yaml``); a route under
any other ``/openapi/v1`` prefix is a leak, not a new domain, until that
configuration declares it.
"""

from __future__ import annotations

from fastapi import FastAPI

#: The ``/openapi/v1`` prefixes the gateway's shipped configuration declares.
#: Extending this tuple is a contract change: it must land together with the
#: matching gateway domain + route_security entries, never alone.
_DECLARED_PREFIXES = (
    "/openapi/v1/bots",
    # The verified caller's own identity — the one operation whose answer is
    # the user. Declared with its gateway domain + route_security entries.
    "/openapi/v1/org",
    "/openapi/v1/spaces",
    "/openapi/v1/work-orders",
    "/openapi/v1/work-order-notifications",
    "/openapi/v1/org/user",
    # Department directory search — a tenant-wide catalogue read, declared with
    # its own gateway domain + route_security entry.
    "/openapi/v1/org/dept",
    # BCS publish-to-users — the external contract path the gateway's
    # `collaboration-publish` domain routes to the backend (pulled out of the
    # broad collaboration→bcs namespace). Declared with its gateway domain +
    # route_security entry.
    "/openapi/v1/collaboration/bots",
    # Task public surface (execute/dashboard/list) — the external contract path
    # the gateway's `collaboration-tasks` domain routes to the backend (pulled
    # out of the broad collaboration→bcs namespace). Declared with its gateway
    # domain + route_security entry.
    "/openapi/v1/collaboration/tasks",
    # Tenant source credentials (W3, #1471) — its own backend domain,
    # declared with the gateway `source-credentials` domain entry and the
    # route_security REFUSED row (human-only).
    "/openapi/v1/source-credentials",
)


def _public_paths(app: FastAPI) -> list[str]:
    return [p for p in app.openapi().get("paths", {}) if p.startswith("/openapi/v1")]


def _is_declared(path: str) -> bool:
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in _DECLARED_PREFIXES
    )


def test_no_internal_routes_leak_into_public_namespace(
    app_with_testing_modules: FastAPI,
) -> None:
    offenders = [
        path
        for path in _public_paths(app_with_testing_modules)
        if not _is_declared(path)
    ]
    assert not offenders, (
        f"routes outside the declared gateway domains leaked into the public "
        f"namespace: {offenders}"
    )


def test_public_surface_is_present(app_with_testing_modules: FastAPI) -> None:
    assert _public_paths(app_with_testing_modules), "no /openapi/v1 routes mounted"
