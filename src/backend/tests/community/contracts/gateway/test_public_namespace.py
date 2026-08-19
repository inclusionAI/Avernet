"""Namespace invariant: the ``/openapi/v1`` surface is only the approved domains.

The gateway forwards ``/openapi/v1/*`` transparently, so nothing internal may
live under that prefix — every public route must sit under one of the approved
domain prefixes: ``/openapi/v1/bots`` (the bot surface, including the harness
operations beneath each addressed bot) and ``/openapi/v1/collaboration/tasks``
(the task-execution + task-discovery surface).
"""

from __future__ import annotations

from fastapi import FastAPI

#: Approved public domain prefixes. Adding one is a design decision — the
#: gateway needs a matching domain, schema artifact, and route_security rule.
_APPROVED_PREFIXES = (
    "/openapi/v1/bots",
    "/openapi/v1/collaboration/tasks",
)


def _public_paths(app: FastAPI) -> list[str]:
    return [p for p in app.openapi().get("paths", {}) if p.startswith("/openapi/v1")]


def test_no_internal_routes_leak_into_public_namespace(
    app_with_testing_modules: FastAPI,
) -> None:
    offenders = [
        path
        for path in _public_paths(app_with_testing_modules)
        if not path.startswith(_APPROVED_PREFIXES)
    ]
    assert not offenders, f"routes outside the approved domains leaked into the public namespace: {offenders}"


def test_public_surface_is_present(app_with_testing_modules: FastAPI) -> None:
    assert _public_paths(app_with_testing_modules), "no /openapi/v1 routes mounted"
