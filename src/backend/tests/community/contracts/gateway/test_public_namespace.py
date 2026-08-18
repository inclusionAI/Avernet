"""Namespace invariant: the ``/openapi/v1`` surface is gateway-forwarded.

The gateway forwards ``/openapi/v1/*`` transparently, so nothing internal may
live under that prefix. First-class public domains are listed in
``_SANCTIONED_PUBLIC_SUBNAMESPACES`` (``bots`` and ``task``); anything else under
``/openapi/v1`` is a leak. Add a sub-namespace here only when a new domain is
deliberately promoted to the public surface — never to silence a leak.
"""

from __future__ import annotations

from fastapi import FastAPI

#: Sub-namespaces the gateway is permitted to forward under ``/openapi/v1``.
#: Each entry is a first-class public domain. ``task`` joins ``bots`` now that
#: the task API has been normalized to ``/openapi/v1/task/*``.
_SANCTIONED_PUBLIC_SUBNAMESPACES = ("/openapi/v1/bots", "/openapi/v1/task")


def _public_paths(app: FastAPI) -> list[str]:
    return [p for p in app.openapi().get("paths", {}) if p.startswith("/openapi/v1")]


def test_no_internal_routes_leak_into_public_namespace(
    app_with_testing_modules: FastAPI,
) -> None:
    offenders = [
        path
        for path in _public_paths(app_with_testing_modules)
        if not path.startswith(_SANCTIONED_PUBLIC_SUBNAMESPACES)
    ]
    assert not offenders, f"unsanctioned routes leaked into the public namespace: {offenders}"


def test_public_surface_is_present(app_with_testing_modules: FastAPI) -> None:
    assert _public_paths(app_with_testing_modules), "no /openapi/v1 public routes mounted"
