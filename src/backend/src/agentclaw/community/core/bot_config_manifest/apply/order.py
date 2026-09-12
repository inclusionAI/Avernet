"""In what order the constructs are applied, and the phase vocabulary.

**This table is complete, and completeness is the point.** It names every
construct the v1 vocabulary defines, including the ones no materialiser can act
on yet. It is the *ordering contract*; which constructs have code behind them is
a separate, sparse fact that lives in :mod:`.registry`. Walking a complete table
and finding a sparse registry is what makes "no materialiser yet" an ordinary
state the orchestrator handles once, rather than a branch naming three
categories by hand.

The table says *what* and *when relative to the others*; it does not say which
half of the creation path a construct lands in. That is :class:`ApplyPhase`,
defined here as shared vocabulary but **answered** per engine family by the
delivery strategies in :mod:`.delivery`.
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

    Two values, lowercase on the wire::

        ApplyPhase.PRE_CONTAINER.value == "pre_container"
        ApplyPhase.ON_CONTAINER.value  == "on_container"

    Created by: each ``DeliveryStrategy.phase_of`` in ``apply/delivery`` —
    the phase is the engine family's answer, not the order table's.
    Consumed by: ``apply/delivery._steps`` as the filter, the creation job
    (which passes one at a time), and the HTTP routes.

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
    """One construct's place in the order::

        ApplyStep(ManifestSection.SCRIPT, 0)
        ApplyStep(ManifestCategory.IDENTITY, 1)

    A pure ordering record: which construct, and where in the walk. The phase
    it lands in is **not** here, because it is not a property of the step — it
    is a function of the engine family, and each ``DeliveryStrategy`` in
    ``apply/delivery`` owns its own phase table.

    Created by: :data:`APPLY_ORDER`.
    Consumed by: ``apply/delivery`` (which phases and filters the steps) and
    ``apply/orchestrator``, which walks the steps in order.
    """

    #: Which construct this step applies.
    construct: ApplyConstruct
    #: The sort key, 0-6, unique across the table. Shared by every family: the
    #: order is the table's, only the phase is family-specific.
    position: int


#: Every construct, in the order an apply walks them.
#:
#: **This REVERSES design §3.4**, which put ``script`` last. The reversal is
#: work-items §2.12's, and the reason is the one :class:`ApplyPhase` states:
#: ``script`` is the only construct needing no container, and on the creation
#: path the row must exist before the start command is composed. A reader of
#: the design who does not find this note here would reasonably implement the
#: design's order and break the creation path.
#:
#: The consequence, stated so nobody has to derive it: on a bot's first boot the
#: script runs **before** any other category is delivered. That is why iteration
#: 1 forbids a manifest's ``script`` from depending on anything else the same
#: manifest declares (§2.12). #1508 removes the restriction in iteration 2 by
#: delivering everything before the container starts.
APPLY_ORDER: tuple[ApplyStep, ...] = (
    ApplyStep(ManifestSection.SCRIPT, 0),
    ApplyStep(ManifestCategory.IDENTITY, 1),
    ApplyStep(ManifestCategory.RESOURCES, 2),
    ApplyStep(ManifestCategory.SKILLS, 3),
    ApplyStep(ManifestCategory.MCP, 4),
    ApplyStep(ManifestCategory.ENGINE_CONFIG, 5),
    ApplyStep(ManifestCategory.CLI_TOOLS, 6),
)

#: Both phases — what the HTTP route passes, and the default everywhere.
ALL_PHASES: frozenset[ApplyPhase] = frozenset(ApplyPhase)

__all__ = ["ALL_PHASES", "APPLY_ORDER", "ApplyPhase", "ApplyStep"]
