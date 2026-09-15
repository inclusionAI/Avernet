"""What started an apply — the vocabulary of ``ApplyReport.trigger``.

Every value is a plain lowercase string, stored verbatim in the apply record's
``trigger`` column and echoed unchanged in the report payload.

========================  =================================================
Value                     Who produces it
========================  =================================================
``explicit``              ``POST …/config-manifest/apply``, through
                          ``adapters/http/…/bots/config_manifest_apply``.
                          Also ``start_apply``'s default, so any caller
                          naming no trigger records this one.
``put``                   ``PUT …/config-manifest`` on an existing bot,
                          through
                          ``adapters/http/…/bots/config_manifest_support``.
``create:pre_container``  The creation job's first phase, through
                          ``creation.ManifestCreationSteps`` and
                          ``create_job``. Applies ``PRE_CONTAINER`` only.
``create:on_container``   The creation job's second phase, same producers.
                          Applies ``ON_CONTAINER`` only.
========================  =================================================

Example::

    ApplyReport(..., trigger="create:pre_container", ...)

One module so a trigger is spelled once. The two creation triggers stay in
``creation.py``, which owns the creation job's recognition of its own phases;
they are re-exported here for readers, not redefined.

On teclaw with the platform-managed switch on, only ``create:pre_container``
occurs: the whole manifest is delivered before the container exists.

Restart and republish are **not** triggers: nothing previously applied is lost
on either path, so a re-apply there was deferred. The column is ``String(32)``;
every value here fits.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)

EXPLICIT = "explicit"
PUT = "put"
CREATE_PRE_CONTAINER = CREATE_PRE_CONTAINER_TRIGGER
CREATE_ON_CONTAINER = CREATE_ON_CONTAINER_TRIGGER

#: Every trigger a report may carry, for tests and readers::
#:
#:     ("explicit", "put", "create:pre_container", "create:on_container")
#:
#: Not enforced anywhere — ``start_apply`` takes a plain ``str``. This is the
#: closed list a reader and a test can check against.
ALL_TRIGGERS: tuple[str, ...] = (
    EXPLICIT,
    PUT,
    CREATE_PRE_CONTAINER,
    CREATE_ON_CONTAINER,
)

#: The apply record's ``trigger`` column width (``apply_models.py``), in
#: characters. ``"create:pre_container"`` is the longest value at 20, so every
#: trigger above fits with room to spare.
TRIGGER_COLUMN_WIDTH = 32


#: The phase each creation trigger delivers — the table above as a lookup.
#: A trigger absent from it delivers the whole document and names no half, so
#: ``.get`` answering ``None`` is the right answer for every other trigger
#: rather than a missing entry.
_PHASE_BY_CREATION_TRIGGER: Mapping[str, ApplyPhase] = MappingProxyType({
    CREATE_PRE_CONTAINER: ApplyPhase.PRE_CONTAINER,
    CREATE_ON_CONTAINER: ApplyPhase.ON_CONTAINER,
})


def require_phase_matches_trigger(trigger: str, phase: ApplyPhase | None) -> None:
    """Refuse a trigger and a phase that contradict each other.

    The table above is the rule, read as a two-way one: the two creation
    triggers each deliver exactly one half of an apply and name which, and
    every other trigger delivers the whole document and has no half to name.
    So a phase is a creation-path argument, and ``None`` is not an omission —
    it is the statement "this apply is not a creation half".

    **The pairing is exact, not merely present.** Asking only whether some
    phase accompanied a creation trigger would accept
    ``create:pre_container`` carrying ``ON_CONTAINER``, which runs the
    container-bound half under a trigger saying the opposite — the pre-container
    work before any container exists, recorded as though it were the other
    phase. So each creation trigger is compared against the one phase it
    delivers, and ``None`` is what every other trigger must carry.

    **Every other trigger.** ``explicit``, ``put``, and any string not in the
    table are simply absent from it, so the lookup answers ``None`` and the
    same comparison then requires ``phase`` to be ``None`` as well: such a call
    passes when it names no half, and raises ``ValueError`` when it names one.
    A *misspelled* creation trigger is absent from the table too, so it reads
    as a non-creation one. In practice that still catches it: a creation caller
    passes the half it is delivering, and a trigger the table does not know
    expects ``None``, so the pair raises. What slips through is only a
    misspelling that also names no phase — which asks for the whole apply and
    gets it. Checking a trigger against :data:`ALL_TRIGGERS` is a separate
    concern from pairing it with a phase, and deliberately not done here: that
    list is "not enforced anywhere" and ``start_apply`` takes a plain ``str``.

    Lives here rather than in the apply service because the pairing is a fact
    about the trigger vocabulary, and this module exists so a trigger is
    spelled once. ``start_apply`` calls it before the lock is taken and before
    an ``apply_id`` is minted, so a mismatch leaves neither a record a caller
    could poll nor a lock the next apply would wait out.

    Raises ``ValueError``: a mismatch is a programming error at a call site,
    not a condition an end user can reach or act on.
    """
    expected = _PHASE_BY_CREATION_TRIGGER.get(trigger)
    if phase is not expected:
        raise ValueError(
            "a creation trigger delivers exactly the half it names, and no "
            f"other trigger names one: trigger={trigger!r} delivers "
            f"phase={expected!r}, but was given phase={phase!r}"
        )


__all__ = [
    "ALL_TRIGGERS",
    "CREATE_ON_CONTAINER",
    "CREATE_PRE_CONTAINER",
    "EXPLICIT",
    "PUT",
    "TRIGGER_COLUMN_WIDTH",
    "require_phase_matches_trigger",
]
