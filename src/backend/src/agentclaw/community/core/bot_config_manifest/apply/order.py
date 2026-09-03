"""In what order the constructs are applied, and which phase each belongs to.

**This table is complete, and completeness is the point.** It names every
construct the v1 vocabulary defines, including the ones no materialiser can act
on yet. It is the *ordering contract*; which constructs have code behind them is
a separate, sparse fact that lives in :mod:`.registry`. Walking a complete table
and finding a sparse registry is what makes "no materialiser yet" an ordinary
state the orchestrator handles once, rather than a branch naming three
categories by hand.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyConstruct
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)


class ApplyPhase(StrEnum):
    """Whether a construct can be materialised before a container exists.

    The split is not organisational. The two halves have **opposite**
    delivery-time constraints, and an orchestrator that ignored that would be
    one W13 has to bypass:

    * ``PRE_CONTAINER`` — a plain database write, no device involved. On the
      creation path it must land *before* ``BaasService._build_create_bot_payload``
      composes the start command, because that function reads the row while
      composing (``baas_service.py``). Too late and the first boot carries no
      script at all.
    * ``ON_CONTAINER`` — resolves a ``DeviceFileSystem`` or a device context and
      **raises if unbound**, so it can only run once the container is up
      (work-items §3.4).

    On an already-running bot the two run back to back and the split is
    invisible. On the creation path they are separated by the whole of container
    provisioning.
    """

    PRE_CONTAINER = "pre_container"
    ON_CONTAINER = "on_container"


@dataclass(frozen=True)
class ApplyStep:
    """One construct's place in the order."""

    construct: ApplyConstruct
    phase: ApplyPhase
    position: int


#: Every construct, in the order an apply walks them.
#:
#: **This REVERSES design §3.4**, which put ``script`` last. The reversal is
#: work-items §2.12's, and the reason is the one :class:`ApplyPhase` states:
#: ``script`` is the only construct needing no container, and on the creation
#: path the row must exist before the start command is composed. Everything else
#: is delivered after the container is up. A reader of the design who does not
#: find this note here would reasonably implement the design's order and break
#: the creation path.
#:
#: The consequence, stated so nobody has to derive it: on a bot's first boot the
#: script runs **before** any other category is delivered. That is why iteration
#: 1 forbids a manifest's ``script`` from depending on anything else the same
#: manifest declares (§2.12). #1508 removes the restriction in iteration 2 by
#: delivering everything before the container starts.
APPLY_ORDER: tuple[ApplyStep, ...] = (
    ApplyStep(ManifestSection.SCRIPT, ApplyPhase.PRE_CONTAINER, 0),
    ApplyStep(ManifestCategory.IDENTITY, ApplyPhase.ON_CONTAINER, 1),
    ApplyStep(ManifestCategory.RESOURCES, ApplyPhase.ON_CONTAINER, 2),
    ApplyStep(ManifestCategory.SKILLS, ApplyPhase.ON_CONTAINER, 3),
    ApplyStep(ManifestCategory.MCP, ApplyPhase.ON_CONTAINER, 4),
    ApplyStep(ManifestCategory.ENGINE_CONFIG, ApplyPhase.ON_CONTAINER, 5),
    ApplyStep(ManifestCategory.CLI_TOOLS, ApplyPhase.ON_CONTAINER, 6),
)

#: Both phases — what the HTTP route passes, and the default everywhere.
ALL_PHASES: frozenset[ApplyPhase] = frozenset(ApplyPhase)

# The ``phase`` column above is the **ARCA** family's table. teclaw delivers by
# artifact and needs no container for any construct, so its strategy
# (``apply/delivery.py``, W8) re-phases every non-script step to
# ``PRE_CONTAINER`` when the platform-managed switch is on. The position column
# is shared; the phase is the family's. ``steps_for`` below stays the ARCA
# reading for callers that predate the seam.


def steps_for(phases: frozenset[ApplyPhase] | None = None) -> tuple[ApplyStep, ...]:
    """The steps in the requested phases, in position order.

    ``None`` means both, which is what an ordinary apply on an existing bot
    wants. W13 passes one at a time — ``PRE_CONTAINER`` before the start command
    is composed, ``ON_CONTAINER`` once the container is up — and gets one report
    from each.
    """
    wanted = ALL_PHASES if phases is None else phases
    return tuple(
        step
        for step in sorted(APPLY_ORDER, key=lambda s: s.position)
        if step.phase in wanted
    )


__all__ = ["ALL_PHASES", "APPLY_ORDER", "ApplyPhase", "ApplyStep", "steps_for"]
