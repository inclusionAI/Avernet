"""Namespace invariant: the ``/openapi/v1`` surface is the bots domain only.

The gateway forwards ``/openapi/v1/*`` transparently, so nothing internal may
live under that prefix — every public route must sit under ``/openapi/v1/bots``.
"""

from __future__ import annotations

from fastapi import FastAPI


def _public_paths(app: FastAPI) -> list[str]:
    return [p for p in app.openapi().get("paths", {}) if p.startswith("/openapi/v1")]


def test_no_internal_routes_leak_into_public_namespace(
    app_with_testing_modules: FastAPI,
) -> None:
    offenders = [
        path
        for path in _public_paths(app_with_testing_modules)
        if not path.startswith("/openapi/v1/bots")
    ]
    assert not offenders, f"non-bots routes leaked into the public namespace: {offenders}"


def test_public_surface_is_present(app_with_testing_modules: FastAPI) -> None:
    assert _public_paths(app_with_testing_modules), "no /openapi/v1/bots routes mounted"
