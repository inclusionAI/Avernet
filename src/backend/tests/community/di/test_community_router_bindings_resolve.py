"""B9 regression guard: the community profile resolves every mounted router dep.

Every HTTP router is mounted unconditionally (all profiles), and each router
dependency is resolved per-request via the injector. A binding that only lands in
the corp column therefore 500s at request time under the community profile —
invisible to CI unless something resolves the *router-facing* types against the
community injector.

This walks the **actual mounted FastAPI app**'s full route dependency tree, so it
catches every injector-backed dependency regardless of how it's declared — a
direct ``Injected(T)`` in a route signature, an ``Injected(T)`` nested inside a
``Depends(...)`` sub-dependency, or a transitive dependency of one of those — and
resolves each against the community injector.

(The app is imported once by ``conftest.py`` at session start under
``DEPLOY_PROFILE=test``; the route *structure* — and thus the set of interfaces —
is profile-independent, so we can enumerate it here and resolve against a freshly
built community injector.)
"""
from __future__ import annotations

import pytest

from agentclaw.community.di import build_injector
from agentclaw.community.di.profile import DeployProfile


def _iter_dependants(dep, seen):
    """Yield ``dep`` and every transitive sub-dependency (FastAPI ``Dependant``)."""
    if id(dep) in seen:
        return
    seen.add(id(dep))
    yield dep
    for sub in getattr(dep, "dependencies", []) or []:
        yield from _iter_dependants(sub, seen)


def _injected_interface(call):
    """Return the interface captured by a fastapi_injector ``Injected(T)``.

    ``Injected(T)`` produces a ``Depends(inject_into_route)`` closure that
    captures ``interface``; pull it back out of the closure freevars.
    """
    code = getattr(call, "__code__", None)
    if code is None or "interface" not in code.co_freevars:
        return None
    try:
        return call.__closure__[code.co_freevars.index("interface")].cell_contents
    except Exception:  # noqa: BLE001
        return None


def _walk_routes(routes):
    """Yield every route, descending into included/mounted sub-routers.

    fastapi >= 0.138 keeps ``include_router`` results as lazy ``_IncludedRouter``
    wrappers in ``app.routes`` (their sub-routes hang off ``original_router.routes``)
    instead of eagerly flattening them into app-level ``APIRoute``s. Older fastapi
    exposed the nested routes directly via ``.routes``. Handle both so the walk
    keeps reaching the real ``APIRoute``s regardless of fastapi version.
    """
    for route in routes:
        yield route
        original = getattr(route, "original_router", None)
        nested = getattr(original, "routes", None) if original is not None else getattr(route, "routes", None)
        if nested:
            yield from _walk_routes(nested)


def _mounted_router_interfaces() -> dict[str, object]:
    """Every distinct injector-backed interface across all mounted routes."""
    from agentclaw.community.adapters.http.app import app

    interfaces: dict[str, object] = {}
    seen: set[int] = set()
    for route in _walk_routes(app.routes):
        dep = getattr(route, "dependant", None)
        if dep is None:  # Mounts / websocket / static routes have no dependant.
            continue
        for d in _iter_dependants(dep, seen):
            iface = _injected_interface(getattr(d, "call", None))
            if iface is not None:
                interfaces[repr(iface)] = iface
    # Coverage floor: the router surface is large; a near-empty walk means the
    # traversal broke (e.g. fastapi internals changed), not that there's nothing.
    assert len(interfaces) >= 40, (
        f"only collected {len(interfaces)} router interfaces — the route walk "
        "likely broke"
    )
    return interfaces


@pytest.mark.unit
def test_community_injector_resolves_every_router_dependency() -> None:
    injector = build_injector(profile=DeployProfile.COMMUNITY)
    failures: list[str] = []
    for name, iface in sorted(_mounted_router_interfaces().items()):
        try:
            injector.get(iface)
        except Exception as e:  # noqa: BLE001 - report every failure together
            failures.append(f"{name}: {type(e).__name__}: {str(e)[:160]}")
    assert not failures, (
        "The community profile cannot resolve these mounted-router dependencies — "
        "a binding is corp-only or construction fails:\n  " + "\n  ".join(failures)
    )


@pytest.mark.unit
def test_guard_has_teeth_unbound_type_raises() -> None:
    """Prove the positive test isn't vacuous: an unbound type must raise under
    ``injector.get`` (so a missing binding would be caught, not silently None)."""
    from typing import Protocol, runtime_checkable

    from agentclaw.community.plugin_api.base import Plugin

    @runtime_checkable
    class _UnboundProbe(Plugin, Protocol):
        def nope(self) -> None: ...

    injector = build_injector(profile=DeployProfile.COMMUNITY)
    with pytest.raises(Exception):
        injector.get(_UnboundProbe)
