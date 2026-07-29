"""Enforcement of the Avernet data-isolation tenant, for any ORM model.

``utils/avernet_tenant`` carries the tenant; this module makes it binding. A
model passed to :func:`register_avernet_tenant_guard` gets two active halves of
one guarantee, both keyed on the request's current tenant and both impossible to
forget because neither lives at a call site:

* **read guard** — a ``do_orm_execute`` listener that appends a tenant ``WHERE``
  clause to every ``SELECT``/``UPDATE``/``DELETE`` touching a guarded model;
* **insert guard** — a ``before_insert`` listener that stamps the tenant on
  every new row (an ``INSERT`` has no ``WHERE``, so the read guard cannot reach
  it) and refuses a row that explicitly names a different tenant.

Stage 1 built both of these welded to ``BotModel`` inside ``plugin_api/models``.
Stage 5 needs them on models owned by other modules, so they live here instead:
``plugin_api`` declares plugin Protocols and must not import ``core`` model
modules, and ``utils/`` is the layer both can already depend on.

One listener covers every guarded model rather than one listener per model, so
there stays a single enforcement point to review. A ``with_loader_criteria``
option naming an entity a statement does not touch is a no-op — asserted by
``tests/community/plugins/test_avernet_tenant_guard.py``, which is what makes
that safe.

A guarded model must declare ``avernet_tenant``:

    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

``server_default`` (not a Python ``default=``) so ``create_all`` emits the same
``DEFAULT 'teamclaw'`` the production DDL applies — backfilling existing rows and
covering any non-ORM insert. The context-aware value on ORM inserts comes from
the insert guard, not the column.
"""
from __future__ import annotations

from typing import Any, Final

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

# The column every guarded model must declare.
AVERNET_TENANT_COLUMN: Final[str] = "avernet_tenant"

# Execution option that opts a statement out of the read guard. Used only by the
# guards' own tests, which need to seed and inspect across tenants.
SKIP_GUARD_OPTION: Final[str] = "skip_avernet_tenant_guard"

# Guarded models, in registration order. A dict rather than a set so the order
# is stable and the criteria a statement receives are deterministic.
_GUARDED_MODELS: dict[type, None] = {}

_READ_GUARD_INSTALLED = False


class CrossTenantInsertError(RuntimeError):
    """An insert named a tenant other than the request's current one."""


def _read_guard(orm_execute_state: Any) -> None:
    """Confine every guarded model's SELECT/UPDATE/DELETE to the current tenant.

    Column and relationship loads are skipped: they only reload an object that a
    prior (already tenant-filtered) SELECT put in the session, so they carry no
    new exposure. This holds while nothing maps a ``relationship()`` to a
    guarded model; if one is ever added, a lazy load would emit an unfiltered
    SELECT — revisit this skip then.
    """
    if orm_execute_state.is_column_load or orm_execute_state.is_relationship_load:
        return
    if not (
        orm_execute_state.is_select
        or orm_execute_state.is_update
        or orm_execute_state.is_delete
    ):
        return
    if orm_execute_state.execution_options.get(SKIP_GUARD_OPTION):
        return
    tenant = get_current_avernet_tenant()
    for model in _GUARDED_MODELS:
        # A direct expression, never a lambda: the lambda form of
        # with_loader_criteria is cached and would pin the first tenant it saw
        # — a leak. Verified in Stage 1.
        orm_execute_state.statement = orm_execute_state.statement.options(
            with_loader_criteria(
                model,
                getattr(model, AVERNET_TENANT_COLUMN) == tenant,
                include_aliases=True,
            )
        )


def _insert_guard(_mapper: Any, _connection: Any, target: Any) -> None:
    """Stamp the current tenant on a new row; reject a conflicting one.

    ``before_insert`` covers ORM unit-of-work inserts (``session.add`` + flush),
    which is the only insert path today across the guarded models. Core/bulk
    inserts (``session.execute(insert(Model))``, ``bulk_insert_mappings``)
    bypass this event and would fall to the column's ``server_default``; none
    exist now — add an equivalent stamp if one is ever introduced.
    """
    current = get_current_avernet_tenant()
    declared = getattr(target, AVERNET_TENANT_COLUMN, None)
    if declared is None:
        setattr(target, AVERNET_TENANT_COLUMN, current)
    elif declared != current:
        raise CrossTenantInsertError(
            f"{type(target).__name__} insert names tenant {declared!r} but the "
            f"request tenant is {current!r}"
        )


def register_avernet_tenant_guard(model: type) -> None:
    """Confine ``model`` to the request's tenant on reads, and stamp it on inserts.

    Call once, immediately after the model class. Idempotent per model, and the
    shared read listener installs once, so a re-import cannot double-register.

    Registered on the ``Session`` class and the mapped class, so both guards
    apply in every runtime — local SQLite, prod OceanBase, and the out-of-tree
    corp ``DatabasePlugin`` this repository does not contain.
    """
    global _READ_GUARD_INSTALLED

    if not hasattr(model, AVERNET_TENANT_COLUMN):
        raise TypeError(
            f"{model.__name__} cannot be tenant-guarded: it declares no "
            f"{AVERNET_TENANT_COLUMN!r} column"
        )

    if not _READ_GUARD_INSTALLED:
        event.listen(Session, "do_orm_execute", _read_guard)
        _READ_GUARD_INSTALLED = True

    if model in _GUARDED_MODELS:
        return
    _GUARDED_MODELS[model] = None
    event.listen(model, "before_insert", _insert_guard)


def guarded_models() -> tuple[type, ...]:
    """The registered models, in registration order. For tests and diagnostics."""
    return tuple(_GUARDED_MODELS)


__all__ = [
    "AVERNET_TENANT_COLUMN",
    "CrossTenantInsertError",
    "SKIP_GUARD_OPTION",
    "guarded_models",
    "register_avernet_tenant_guard",
]
