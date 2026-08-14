"""Substitute named methods of a wired service, through the injector.

Some router branches exist for failures the system can only suffer, never
be talked into: a repository write that dies mid-transaction, an upstream
that stops answering. No request body reaches them, so an endpoint case
that wants to pin the router's error mapping has to inject the failure
somewhere.

The old way was ``unittest.mock.patch.object(type(svc), "m", ...)``, which
edits the production class for the rest of the process — it outlives the
case unless something remembers to stop it, and
:mod:`test_no_mock_in_endpoint_tests` now forbids it in the case tree.

These helpers do the same job through the dependency graph instead:

* build a **subclass** of whatever implementation the injector wired, with
  only the named methods overridden;
* re-clothe it with that instance's own collaborators, so every other line
  of behaviour is the production one;
* bind it for the requested key on the **per-test injector**, which the
  fixture throws away at teardown.

The production class is never touched, so nothing can leak into the next
test, and the handler resolves the stand-in exactly as it resolves the
real service.

Reach for these only for a failure (or a boundary outcome) that no input
can produce. When the endpoint *can* be driven into the branch — a missing
row, a denied permission, a malformed body — seed that instead; it tests
more and explains itself better.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping, TypeVar


T = TypeVar("T")


def bind_overrides(
    world: Any,
    key: type[T],
    overrides: Mapping[str, Callable[..., Any]],
    *,
    also_bind: tuple[type, ...] = (),
) -> T:
    """Serve ``key`` from the wired instance with several methods replaced.

    The plural form of :func:`bind_method`, for a service whose whole surface
    a case has to stand in for. One subclass carries every override, so the
    handler still sees a single coherent object rather than a pile of
    independently patched attributes.
    """
    wired = world.get(key)
    base = type(wired)
    for method, impl in overrides.items():
        original = getattr(base, method, None)
        if original is None:
            raise AttributeError(f"{base.__name__} has no method {method!r} to replace")
        if inspect.iscoroutinefunction(original) and not inspect.iscoroutinefunction(
            impl
        ):
            raise TypeError(
                f"{base.__name__}.{method} is async; give it an async impl so "
                "callers can await the stand-in the same way"
            )

    stand_in_cls = type(f"{base.__name__}StandIn", (base,), dict(overrides))
    stand_in = stand_in_cls.__new__(stand_in_cls)
    stand_in.__dict__.update(wired.__dict__)
    for bound_key in (key, *also_bind):
        world.injector.binder.bind(bound_key, to=stand_in, scope=None)
    return stand_in


def bind_method(
    world: Any,
    key: type[T],
    method: str,
    impl: Callable[..., Any],
    *,
    also_bind: tuple[type, ...] = (),
) -> T:
    """Serve ``key`` from the wired instance with ``method`` replaced by ``impl``.

    ``impl`` is called with the stand-in as its first argument, exactly as a
    normal method would be, so it can reach the service's own collaborators
    (``self._repo`` and friends) to record what a real implementation would
    have written.

    ``also_bind`` names the other injector keys that must resolve to the same
    stand-in. A service reached both as its Protocol and as its concrete class
    — the publish flow is reached both ways, by the routers and by the durable
    task handlers — would otherwise be substituted on only one of those paths.

    Returns the stand-in, so a case can assert against it afterwards.
    """
    return bind_overrides(world, key, {method: impl}, also_bind=also_bind)


def bind_failing_method(
    world: Any,
    key: type[T],
    method: str,
    error: BaseException,
    *,
    also_bind: tuple[type, ...] = (),
) -> T:
    """Serve ``key`` with ``method`` raising ``error`` — the failure-mapping seam.

    Use for the router branches that only a genuine infrastructure fault can
    reach, and name the error the production code would actually raise so the
    case documents the mapping it pins (``BotPublishServiceError`` → 403,
    anything unexpected → 500).
    """
    wired = world.get(key)
    original = getattr(type(wired), method, None)

    if original is not None and inspect.iscoroutinefunction(original):
        async def _fail(_self, *_args, **_kwargs):
            raise error
    else:
        def _fail(_self, *_args, **_kwargs):
            raise error

    return bind_method(world, key, method, _fail, also_bind=also_bind)
