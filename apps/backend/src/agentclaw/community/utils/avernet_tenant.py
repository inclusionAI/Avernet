"""The Avernet data-isolation tenant, carried per request.

Every bot read and write is confined to one *tenant* so the public API can
serve a registered external tenant without ever seeing, or being seen by, the
existing internal one. This module owns the tenant carrier: a task-local
``ContextVar`` set once per request (by ``AvernetTenantMiddleware``) and read
wherever enforcement happens (the ``BotModel`` guards in ``plugin_api/models``).

It is the exact analogue of ``utils/env_utils`` — a low-level, dependency-free
carrier for a request-scoped scalar — and mirrors how the community tracer
carries its per-request trace id (``plugins/community/tracer``).

NOTE — this is NOT the poolab sandbox-allocator ``tenant`` that appears in
``core/service_bot/services/baas_service`` (the ``tenant`` / ``tenant_id`` on
the BaaS provisioning path). That is an unrelated concept: a hint forwarded to
the sandbox allocator, not a data-isolation key. The ``avernet_tenant`` prefix
throughout this feature keeps the two from being conflated.
"""
from __future__ import annotations

import functools
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Final, Iterator, ParamSpec, TypeVar

# The fallback tenant. It owns every bot record that predates this feature and
# every request that does not identify a tenant (the entire internal API, plus
# background work). ``teamclaw`` is the internal product's own name; it must
# never be offered to an external registered tenant.
DEFAULT_AVERNET_TENANT: Final[str] = "teamclaw"

# Task-local current tenant. The default makes ``get_current_avernet_tenant`` a
# total function: there is always a tenant, even outside a request.
_CURRENT_TENANT: ContextVar[str] = ContextVar(
    "avernet_tenant", default=DEFAULT_AVERNET_TENANT
)

P = ParamSpec("P")
R = TypeVar("R")


def get_current_avernet_tenant() -> str:
    """Return the tenant the current request belongs to.

    Total function — returns :data:`DEFAULT_AVERNET_TENANT` outside any request
    (background/scheduled work, ad-hoc scripts), never ``None``.
    """
    return _CURRENT_TENANT.get()


@contextmanager
def avernet_tenant_scope(tenant_id: str) -> Iterator[None]:
    """Bind ``tenant_id`` for the duration of the ``with`` block.

    The reset in ``finally`` always runs — including when the body raises — so a
    tenant can never survive its scope or leak into the next request that reuses
    the worker.
    """
    token = _CURRENT_TENANT.set(tenant_id)
    try:
        yield
    finally:
        _CURRENT_TENANT.reset(token)


def bind_current_avernet_tenant(fn: Callable[P, R]) -> Callable[P, R]:
    """Wrap ``fn`` so it runs under the tenant current at *bind* time.

    ``threading.Thread`` does not copy context vars into the new thread, so a
    thread spawned during a request would otherwise fall back to the default
    tenant. Wrapping its target with this captures the request's tenant now and
    re-establishes it inside the thread when the target runs.

    (``asyncio.create_task`` needs no wrapping — it copies the current context.)
    """
    tenant = get_current_avernet_tenant()

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with avernet_tenant_scope(tenant):
            return fn(*args, **kwargs)

    return wrapper
