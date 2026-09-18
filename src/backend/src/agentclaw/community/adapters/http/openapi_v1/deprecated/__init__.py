"""The addresses this surface used to have, kept answering while callers move.

Every operation re-addressed by the bot-first change is still reachable at the
address it had before, with the contract it had before: the same parameter names
in the same places, the same schemas, the same status codes. A legacy operation
is **not** an alias for its replacement — where the contract moved as well as
the address (the routines create's body bot, the skills group's
``owner_entity_id``, the approvals write's ``session_key``) this package owns
the old shape and translates.

One entry retires a **method** rather than an address: the auth-status poll
became a POST at the same path (it creates the bot, so it was never a read),
and the GET spelling — query parameters and all — survives here the same way
(``auth_status.py``).

Nothing here is removed by the change that created it. Removal is a later,
per-address decision made when the access log shows no traffic, and it is meant
to be cheap: delete a module, delete its line in :func:`build_deprecated_router`,
and the address is gone along with the old contract it carried.

Two things are deliberately *not* shared with the current surface:

- **The old request models.** ``RoutineCreate`` and ``ApprovalModeSet`` — the
  names those bodies published, kept here because a component name is part of
  what a generated client is written against — live here, not in the schemas
  modules they came from, so deleting this package deletes them.
- **The handler-side grant check.** ``TODO(#960)`` recorded it as two mechanisms
  doing one job. The current surface has fewer of them, not one: four
  ``{skill_id}`` operations still check in their handlers because their bot's
  owner is only known after the skill is read (``admission.SKILL_SCOPED_OPERATIONS``).
  What is scoped to *this* package is the rest — the legacy skills collection
  and item operations and the legacy routines create, whose bot is in the body
  or nowhere at all — and that part dies with these addresses.
"""

from __future__ import annotations


from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION

from ._shim import LEGACY_ROUTES
from . import approvals as _approvals
from . import auth_status as _auth_status
from . import bots as _bots
from . import engine_runtime as _engine_runtime
from . import resources as _resources
from . import routines as _routines
from . import skills as _skills

#: Mounted with the engine-runtime response table, like their replacements.
ENGINE_RUNTIME_GROUPS = _engine_runtime.ENGINE_RUNTIME + [_approvals.router]

#: Mounted with the addressed-bot grant, like their replacements: the
#: retiring identity addresses still carry ``{bot_id}`` on the path and
#: resolve ``OwnerIdDep``, so they take the same check-and-grant pair the
#: current address does.
ADDRESSED_GROUPS = _engine_runtime.ADDRESSED

#: Mounted grant-checked (own-bot): the owners of these operations' bots are
#: themselves — the resources and routines retiring shims pin the owner to the
#: caller (``_requery.pin_owner_to_user``), so ``require_granted_own_bot``'s
#: caller-is-the-owner grant is the one congruent with what the handler reads.
GRANT_CHECKED_GROUPS = [
    _auth_status.router,
    _bots.router,
    _resources.router,
    _routines.router,
]

#: Mounted without the grant check, because their bot is not where it can be
#: seen. Every route in this group checks it itself, first, before acting.
SELF_CHECKED_GROUPS = [_skills.router, _routines.create_router]

#: The retiring operations that check the grant themselves, named so the
#: admission inventory can tell "checks somewhere else" from "does not check".
#: It is the old seven minus the one the addressing change fixed on both
#: surfaces, and it is empty the day this package is deleted.
SELF_CHECKED_ROUTES = frozenset(
    (method, path)
    for router in SELF_CHECKED_GROUPS
    for route in router.routes
    for method in getattr(route, "methods", ())
    if (path := getattr(route, "path", None))
)


def _inherit_admission_modes() -> None:
    """Give every legacy address the admission mode of the one that replaced it.

    Derived rather than restated. ``admission.py`` is a table of decisions about
    who may call what, and a legacy address is not a new decision — it is the
    same operation at its old address, so writing the mode out again would be a
    second copy of one decision, free to drift from the first.

    One exception, and it is visible as such: the pinned retiring addresses
    (``admission.py``'s "Pinned retiring addresses" rows) keep the own-bot
    mode their shims enforce — their replacements moved onto the addressed
    grant, but the retiring shims resolve the owner as the caller and must not
    carry a mode promising more. Already-decided rows are filled first, so a
    written row is an override by construction rather than by silent luck.

    Run at import, before any request: ``ADMISSION`` is read per request by
    ``require_principal``'s admission check, and an operation missing from it
    is refused. ``test_admission_inventory`` is what fails if a legacy address
    ever names a replacement that is not itself in the table.
    """
    for (method, legacy_path), replacement in LEGACY_ROUTES.items():
        if (method, legacy_path) in ADMISSION:
            continue
        mode = ADMISSION.get((method, replacement))
        if mode is not None:
            ADMISSION[(method, legacy_path)] = mode


_inherit_admission_modes()

__all__ = [
    "ADDRESSED_GROUPS",
    "ENGINE_RUNTIME_GROUPS",
    "GRANT_CHECKED_GROUPS",
    "LEGACY_ROUTES",
    "SELF_CHECKED_GROUPS",
    "SELF_CHECKED_ROUTES",
]
