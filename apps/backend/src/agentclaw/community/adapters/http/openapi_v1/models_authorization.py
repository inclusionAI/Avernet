"""Authorization declarations, separate from route enforcement."""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.bot_collaborator.models import PermissionLevel


class _EditLock:
    """Require the caller to hold the Bot edit lock after authorization."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "EDIT_LOCK"


EDIT_LOCK = _EditLock()


@dataclass(frozen=True)
class Check:
    """**The seam enforces this**, at ``level`` or above; OWNER always passes.

    Getting the level wrong here refuses callers the surface means to admit, or
    admits callers it means to refuse — this is the enforcing column, unlike
    :class:`ServiceChecked`'s.
    """

    level: PermissionLevel
    edit_lock: _EditLock | None = None

    def __post_init__(self) -> None:
        """Refuse ``NONE``, which would be a gate that never refuses.

        ``_level`` returns ``NONE`` for every unresolvable case — absent bot,
        unreadable collaborator table, unwired injector — and the gate compares
        ``level < rule.level``. With ``NONE`` as the bar that comparison is
        false for exactly those cases, so a one-word typo in the table would
        turn the fail-closed gate into one that admits precisely the callers it
        exists to stop. Rejected at construction rather than left to a test,
        because the table is a literal: this raises while the module imports.
        """
        if self.level is PermissionLevel.NONE:
            raise ValueError(
                "Check(PermissionLevel.NONE) is not a bar — it admits every "
                "caller the gate would otherwise refuse. Name the level the "
                "operation actually requires."
            )
        if self.edit_lock not in (None, EDIT_LOCK):
            raise ValueError(
                "Check's second argument must be EDIT_LOCK when the operation "
                "requires the Bot edit lock."
            )


@dataclass(frozen=True)
class NoCheck:
    """**Nothing to verify**, deliberately. ``reason`` says which kind.

    Required rather than optional: an empty reason turns a decision into an
    oversight that reads exactly like a decision.
    """

    reason: str


@dataclass(frozen=True)
class ServiceChecked:
    """Scaffolding: a service enforces this, elsewhere. → ``Check(level)``.

    The row exists because every operation must have one — this is how an
    operation says "I am covered, just not here" — and it records the bar to
    preserve so the migration is a comparison rather than a guess. ``where`` is
    an abbreviated module path (``…`` for the package prefix), resolved by the
    inventory test.
    """

    level: PermissionLevel
    where: str


class _Scaffold:
    """A mode with nothing to parameterise, named for readable failures."""

    __slots__ = ("_name", "__weakref__")

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return self._name


#: Scaffolding: no collaborator dimension decided. The operation resolves the
#: bot as ``(bot_id, caller)``, so only the owner reaches it at all. Becomes
#: ``Check(level)`` once #906 / #907 decide the bar — which is a policy change,
#: not a mechanical migration: collaborators start getting through.
OWNER_SCOPED = _Scaffold("OWNER_SCOPED")

#: Scaffolding: a retiring address under ``deprecated/``. ``_relocate`` and
#: ``_requery`` re-register the *replacement's own endpoint function* at the old
#: path, so there is no second handler here and no second decision to record:
#: the row that governs one of these is the replacement's row. The whole set
#: disappears when the package is deleted.
#:
#: Named ``SELF_CHECKED`` until it was pointed out that nothing here checks
#: itself — the name asserted a property no legacy route has, and collided with
#: ``deprecated``'s unrelated ``SELF_CHECKED_ROUTES``, which really does mean a
#: router that performs its own grant check.
INHERITED = _Scaffold("INHERITED")

#: The modes that must be empty for the surface to have reached its final shape.
#: ``_Scaffold`` covers both sentinels, so a fourth scaffolding sentinel added
#: later is counted by :func:`scaffolding_row_count` without touching this.
SCAFFOLDING_MODES = (ServiceChecked, _Scaffold)

Authorization = Check | NoCheck | ServiceChecked | _Scaffold
