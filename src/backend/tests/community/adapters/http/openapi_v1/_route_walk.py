"""Walking the assembled public surface the way the application serves it.

``include_router`` stores a lazy wrapper rather than copying routes, and the
*original* router does not carry the dependencies added at include time — so
walking ``original_router`` would miss exactly the declarations these checks
exist to verify, and they would pass while the wiring was absent. Reading the
effective contexts is what makes them checks about the assembled surface.

Lifted from ``test_admission_inventory.py``, which grew these first and still
carries its own copy. Shared here rather than copied a third and fourth time by
the authorization files; folding that older copy into this module is a tidy-up
for whoever next touches it, not part of this change.
"""

from __future__ import annotations


def effective_routes(router):
    """Every operation as the application will really serve it."""
    found = []
    for route in getattr(router, "routes", []):
        if hasattr(route, "effective_route_contexts"):
            found.extend(route.effective_route_contexts())
        elif hasattr(route, "dependant"):
            found.append(route)
    return found


def path_of(ctx) -> str:
    """The route's path, for HTTP and WebSocket alike.

    A socket route's effective context carries an empty ``path`` — the merged
    view is built for HTTP — so its path comes off the original route. Falling
    back rather than skipping keeps the socket plane inside the inventory.
    """
    return ctx.path or getattr(getattr(ctx, "original_route", None), "path", "")


def original_route_of(ctx):
    """The route object as its module constructed it, or the context itself."""
    return getattr(ctx, "original_route", None) or ctx


def operations(router):
    """``(method, path)`` for every effective operation, WebSockets included."""
    seen = []
    for ctx in effective_routes(router):
        methods = set(getattr(ctx, "methods", None) or {"WEBSOCKET"})
        path = path_of(ctx)
        assert path, f"could not determine a path for {ctx!r}"
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            seen.append(((method, path), ctx))
    return seen


def dependant_of(ctx):
    """The dependency tree to inspect, for HTTP and WebSocket alike."""
    return ctx.dependant or getattr(
        getattr(ctx, "original_route", None), "dependant", None
    )


def depends_on(dependant, target) -> bool:
    """Whether ``target`` appears anywhere in this dependency tree."""
    if dependant is None:
        return False
    if dependant.call is target:
        return True
    return any(depends_on(sub, target) for sub in dependant.dependencies)
