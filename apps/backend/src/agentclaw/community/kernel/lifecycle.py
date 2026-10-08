"""Lifecycle Protocol — the canonical startup/shutdown contract.

Every DI-managed component that needs to do work at process boot or
shutdown implements :class:`Lifecycle` (typically by inheriting
:class:`LifecycleBase` and overriding only the hooks it cares about).
The composition root discovers participants by introspecting the
injector and runs the hooks in a two-phase, concurrent pipeline.

Two phases, each direction
--------------------------

::

    Startup:   bootstrap()   →   startup()
    Shutdown:  shutdown()    →   teardown()

Within a phase, every participant's hook runs concurrently
(``asyncio.gather``). The next phase only begins after every coroutine
of the previous phase has resolved. This lets infrastructure-tier
components (e.g. DB schema creation) finish via ``bootstrap()`` before
service-tier components query the DB in ``startup()``.

Failure semantics
-----------------

* Setup direction (``bootstrap``, ``startup``): **fail-fast** — first
  exception aborts the boot via :func:`asyncio.gather` cancellation;
  the FastAPI lifespan re-raises, and the process exits non-zero.
* Teardown direction (``shutdown``, ``teardown``): **log-and-continue**
  — every component's hook is awaited via
  ``gather(..., return_exceptions=True)``; exceptions are logged per
  component, but they do not stop other shutdowns.

Ordering
--------

* **Within a phase**: NONE. If component *A* needs *B* during the same
  phase, *A* declares *B* as a constructor dependency — the DI graph
  resolves before any hook runs.
* **Across phases**: strict. ``bootstrap()`` for all participants
  completes before any ``startup()`` runs. ``shutdown()`` for all
  participants completes before any ``teardown()`` runs.

Discovery
---------

:func:`discover_lifecycle_participants` walks the injector's bound
interfaces, resolves each to its singleton instance, and returns every
distinct instance that satisfies :class:`Lifecycle`. Deduplication is
by ``id(instance)`` so a plugin that's reachable from multiple
bindings (e.g. via a router) runs its hooks exactly once.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from injector import Injector


@runtime_checkable
class Lifecycle(Protocol):
    """Canonical lifecycle contract.

    All four hooks are async. A component that only needs one or two
    should inherit :class:`LifecycleBase` and override the ones it
    cares about; the rest stay as no-ops.

    NOTE: ``@runtime_checkable`` ``isinstance(x, Lifecycle)`` succeeds
    only when *every* attribute below exists on ``x``. A class missing
    one would be silently skipped by discovery. ``LifecycleBase``
    guarantees all four are present.
    """

    async def bootstrap(self) -> None:
        """Infrastructure setup. Runs in phase 1 of startup.

        Use for prerequisites that other components need to exist
        before they can do their own startup work — e.g. creating DB
        tables, acquiring distributed locks, opening shared file
        handles.

        Every participant's ``bootstrap()`` is launched concurrently
        via ``asyncio.gather``. The next phase (``startup()``) only
        begins after **all** ``bootstrap()`` coroutines have resolved,
        so service-tier components can safely assume infra-tier
        ``bootstrap()`` has completed.

        Failure: fail-fast. The first exception aborts the boot and
        propagates out of the FastAPI lifespan — no ``startup()``
        runs, the process exits non-zero. Pick this hook only when
        the component genuinely cannot proceed without the work
        succeeding.
        """
        ...

    async def startup(self) -> None:
        """Service setup. Runs in phase 2 of startup, after every
        ``bootstrap()`` has completed.

        Use for service-tier work: subscribing to the event bus,
        starting periodic tasks, querying initial state, re-allocating
        resources from a prior process. May freely depend on anything
        a ``bootstrap()`` would have set up (DB tables exist, locks
        held, etc.).

        Every participant's ``startup()`` is launched concurrently via
        ``asyncio.gather``. Order within the phase is undefined; if
        component A needs B inside this phase, A declares B as a
        constructor dependency (the DI graph resolves before any hook
        runs).

        Failure: fail-fast. Same semantics as ``bootstrap()``.
        """
        ...

    async def shutdown(self) -> None:
        """Service teardown. Runs in phase 1 of shutdown, mirror of
        ``startup()``.

        Use for stopping the work ``startup()`` started: stop periodic
        tasks, unsubscribe from the event bus, release per-bot
        resources, kill spawned subprocesses. Infrastructure that
        ``shutdown()`` itself depends on (DB connections, etc.) is
        still available — those get released in ``teardown()``.

        Every participant's ``shutdown()`` is launched concurrently
        via ``asyncio.gather(return_exceptions=True)``. The next phase
        (``teardown()``) only begins after **all** ``shutdown()``
        coroutines have resolved (succeeded or raised).

        Failure: log-and-continue. Per-component exceptions are logged
        and discarded so cleanup doesn't get stuck — one broken
        ``shutdown()`` does not prevent other participants from
        shutting down or from ``teardown()`` running.
        """
        ...

    async def teardown(self) -> None:
        """Infrastructure teardown. Runs in phase 2 of shutdown,
        mirror of ``bootstrap()``.

        Use for releasing resources that other components' shutdowns
        depended on — closing DB engines, releasing file locks,
        shutting down thread / connection pools. By this point every
        participant's ``shutdown()`` has returned, so no service-tier
        code should still need the infrastructure.

        Every participant's ``teardown()`` is launched concurrently
        via ``asyncio.gather(return_exceptions=True)``.

        Failure: log-and-continue. Same semantics as ``shutdown()``.
        """
        ...


class LifecycleBase:
    """Concrete base with no-op defaults for all four hooks."""

    async def bootstrap(self) -> None:
        pass

    async def startup(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def teardown(self) -> None:
        pass


def discover_lifecycle_participants(injector: Injector) -> list[Lifecycle]:
    """Walk injector bindings, return distinct instances satisfying ``Lifecycle``.

    Implementation note: uses ``injector.binder._bindings`` which is a
    private attribute. The library version is pinned via ``uv.lock``;
    this single helper is the only place to update if the underlying
    API ever changes.
    """
    seen: set[int] = set()
    out: list[Lifecycle] = []
    for interface in list(injector.binder._bindings.keys()):
        try:
            instance = injector.get(interface)
        except Exception:
            # A binding that fails to resolve at discovery time (e.g.
            # because its provider raises) is skipped. Real wiring
            # bugs surface elsewhere (eager_check_critical_bindings).
            continue
        if isinstance(instance, Lifecycle) and id(instance) not in seen:
            seen.add(id(instance))
            out.append(instance)
    return out
