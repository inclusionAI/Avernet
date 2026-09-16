"""How a manifest reaches a bot, per engine family (W8, #1476).

Two engine families deliver a bot's configuration by opposite mechanisms, and
the apply engine must not know which it is running for:

* **ARCA** boots from a start command and takes everything else as writes into
  a live container. ``script`` is baked into the start command, so it is the
  one construct that must exist *before* the container; every other construct
  resolves a device and can only land *after* it.
* **teclaw** boots from a composed artifact and applies it in full before it
  reports ready (W12 contract, A4). The platform is the source of truth for
  what a manifest applies (spec D-3): every construct is materialised into
  platform state — database rows, and the bot-data object store for files —
  and the artifact is the delivery. Nothing needs the container, so nothing
  waits for it. ``script`` is unsupported on teclaw.

A :class:`DeliveryStrategy` owns exactly the four things that differ: the phase
each construct belongs to, the write ports the materialisers are handed, the
creation sequence the W13 job runs, and the step that closes an apply. The
orchestrator sees phases and the materialisers see ports; neither learns the
family. Adding a family is a strategy, not a fork of five materialisers.

**Three strategies, not two, and none of them holds a switch.** Until the
teclaw engine supports the artifact's ``ownership`` map, a deployment may run
teclaw the way it ran before W8 — every non-script construct after the
container, through the same device-backed ports ARCA uses. That is
:class:`TeclawDeviceDelivery`; the artifact-delivered shape is
:class:`TeclawPlatformDelivery`. Which of the two a deployment runs is a
:class:`TeclawDeliveryMode`, read once from
``user_config.bot_config_manifest.teclaw_platform_managed`` (default off) by
:func:`teclaw_delivery_mode_from_config` and turned into *one built strategy*
by :data:`TECLAW_DELIVERY_BY_MODE` in the composition root.

That is the whole of the mode's life: a yaml scalar, a table row, an object.
No strategy carries it as a field and nothing re-reads it per apply, because
the answer cannot change while the process runs — a deployment-time fact is
settled at deployment time, and a component that held it would have to be
asked the same question on every call, in front of every bot. What each
strategy holds instead is only what its own shape needs: the platform one has
store-backed ports and a closing redeliver, the device one has device-backed
ports and no closing step at all. Neither can be handed the other's
collaborators, because neither takes them.

**Nothing here branches on a family or a mode either.** ``phase_of`` is a table
lookup in a per-strategy phase map (:data:`_ARCA_PHASES`,
:data:`_TECLAW_PLATFORM_PHASES`, :data:`_TECLAW_DEVICE_PHASES`), selecting the
teclaw implementation is a lookup in :data:`TECLAW_DELIVERY_BY_MODE`, and
:class:`DeliveryStrategyFactory` is a lookup in a
:class:`EngineFamily`-keyed mapping the composition root hands it. Each table
is checked for exhaustiveness at import — the discipline
``source_fetchers.FETCHER_TYPES`` sets — so a family or a construct with no
answer fails at boot rather than ``KeyError``-ing mid-apply with the bot's
lock held.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from abc import abstractmethod
from typing import Any, Awaitable, Callable, ClassVar, Mapping, Optional, Protocol

from agentclaw.community.core.ports.activation_port import (
    ActivationPort,
)
from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.ports.identity_file_port import (
    IdentityFilePort,
)
from agentclaw.community.core.ports.resource_file_port import (
    ResourceFilePort,
)
from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestCategory
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    CliToolService,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_auth_service_protocol import MCPAuthServiceProtocol
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.bot_config_manifest.apply.order import (
    APPLY_ORDER,
    ApplyPhase,
    ApplyStep,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyConstruct,
    ApplyReport,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestSection
from agentclaw.community.core.bot_config_manifest.delivery_mode import (
    TECLAW_PLATFORM_MANAGED_KEY,
    TeclawDeliveryMode,
    teclaw_delivery_mode_from_config,
)

# ``TECLAW_PLATFORM_MANAGED_KEY`` and ``teclaw_delivery_mode_from_config`` are
# imported to be re-exported (they are named in ``__all__``): the deployment
# vocabulary lives in its own leaf module, because the typed config cluster
# reads it and cannot pull this graph in behind it, while the seam that turns a
# mode into a strategy is here.

class EngineFamily(StrEnum):
    """Which delivery mechanism a bot's engine boots from.

    The key a strategy is selected by, and the word a report or a log uses for
    it. Two values, lowercase on the wire::

        EngineFamily.ARCA.value == "arca"

    Not the engine type: many engine types (``claude_code``, ``openclaw``, …)
    are ARCA. The mapping from one to the other belongs to the engine
    authority, which the composition root adapts through
    :func:`family_from_engine_test`.
    """

    ARCA = "arca"
    TECLAW = "teclaw"


class CreationSequence(StrEnum):
    """The order a W13 creation runs its steps in, per family.

    Each value names its steps in the order they run; the two differ in
    *where the container is created* relative to the manifest phases.
    """

    #: pre-container phase → create the bot **and provision it** → wait for
    #: ``ACTIVE`` → post-container phase. The manifest is applied in two
    #: halves around the container. ARCA's sequence, and the device-backed
    #: teclaw shape's.
    CREATE_BETWEEN_PHASES = "create_between_phases"
    #: create the bot **record only** → the single pre-container phase writes
    #: platform state against it → provision (which composes the first
    #: artifact from that state) → wait for ``ACTIVE``. No post-container
    #: phase: everything was delivered before the container existed.
    #: Platform-managed teclaw.
    RECORD_APPLY_PROVISION = "record_apply_provision"


@dataclass(frozen=True)
class MaterialiserPorts:
    """The write targets a strategy hands ``build_materialisers``.

    Ten fields, matching that function's keyword arguments name for name, so
    :meth:`as_kwargs` can splat them straight in. A strategy differs from
    another by which **objects** sit behind these names, never by which
    materialisers exist: ARCA binds device-backed services, platform-managed
    teclaw binds store-backed ports, and each field is typed by the narrow port
    the materialiser calls through so the two are interchangeable by shape.

    Created by: the composition root, as the two lazy bundles it binds — the
    device-backed one and the store-backed one.
    Consumed by: ``apply/registry.build_materialisers``, via
    :meth:`as_kwargs`.
    """

    script_service: BotStartupScriptServiceProtocol
    activation_service: ActivationPort
    mcp_auth_service: MCPAuthServiceProtocol
    identity_service: IdentityFilePort
    upload_service: SkillPackageUploadPort
    capability_reader: BotCapabilityStateReaderProtocol
    package_validator: SkillPackageValidator
    entry_fetcher: DeclaredSourceResolver
    resource_service: ResourceFilePort
    #: W9. One field for a whole category, because the service already holds
    #: the family's delivery port — so the ``cli_tools`` materialiser takes one
    #: dependency and the family difference stays here, where W6 put it.
    cli_tool_service: CliToolService

    def as_kwargs(self) -> dict[str, Any]:
        """The same ten fields as a plain dict, ready to splat into
        ``build_materialisers(**ports.as_kwargs())``. Written out by hand
        rather than derived, so adding a field here without adding a
        materialiser parameter is a visible edit."""
        return {
            "script_service": self.script_service,
            "activation_service": self.activation_service,
            "mcp_auth_service": self.mcp_auth_service,
            "identity_service": self.identity_service,
            "upload_service": self.upload_service,
            "capability_reader": self.capability_reader,
            "package_validator": self.package_validator,
            "entry_fetcher": self.entry_fetcher,
            "resource_service": self.resource_service,
            "cli_tool_service": self.cli_tool_service,
        }


class DeliveryStrategy(Protocol):
    """What differs between engine families, and nothing else.

    The members it owns, and how the three implementations answer:

    =====================  ================  ==================  =================
    Member                 ``ArcaDelivery``  ``TeclawPlatform``  ``TeclawDevice``
    =====================  ================  ==================  =================
    ``family``             ``ARCA``          ``TECLAW``          ``TECLAW``
    ``creation_sequence``  ``CREATE_``       ``RECORD_APPLY_``   ``CREATE_``
                           ``BETWEEN_``      ``PROVISION``       ``BETWEEN_``
                           ``PHASES``                            ``PHASES``
    ``phase_of(step)``     ``_ARCA_``        ``_TECLAW_``        ``_TECLAW_``
                           ``PHASES``        ``PLATFORM_``       ``DEVICE_``
                                             ``PHASES``          ``PHASES``
    ``needs_container()``  ``True``          ``False``           ``True``
    ``ports()``            device-backed     store-backed        device-backed,
                                                                 teclaw CLI port
    ``finish()``           ``None``          one whole-artifact  ``None``
                                             redeliver
    =====================  ================  ==================  =================

    ``TeclawDeviceDelivery`` answers the ARCA column for every row except
    ``family`` and the CLI port — that is the point of it. It exists so a
    deployment whose engine has not shipped the ``ownership`` map keeps the
    behaviour it had, and it disappears the day the mode does.

    Created by: the composition root, one per row of the mapping it hands
    :class:`DeliveryStrategyFactory`.
    Consumed by: the apply service (``ports``, ``finish``), the orchestrator
    (``steps_for``), and the creation job (``creation_sequence``,
    ``needs_container``).

    Implemented by the three classes below, which subclass it explicitly so the
    implementations are one jump away — and every member here is
    ``@abstractmethod``, so that subclassing is load-bearing rather than
    decorative. The backend runs no static type checker (the reason
    ``core/ports/identity_file_port.py`` records): without abstract members a
    family that dropped ``finish`` would inherit the ``...`` stub and return
    ``None``, silently skipping the closing redeliver of every apply it ran.
    """

    @property
    @abstractmethod
    def family(self) -> EngineFamily:
        """The engine family this strategy delivers for.

        Example: ``ArcaDelivery(...).family is EngineFamily.ARCA``. Both teclaw
        strategies answer ``TECLAW`` — the family is what a bot *is*, the mode
        is what the deployment *runs*, and a report or a log wants the former.
        """
        ...

    @property
    @abstractmethod
    def creation_sequence(self) -> CreationSequence:
        """Which of the two creation orders this family runs."""
        ...

    @abstractmethod
    def phase_of(self, step: ApplyStep) -> ApplyPhase:
        """Which phase this family delivers the step's construct in.

        The phase table is the strategy's own — :data:`ApplyStep` carries no
        phase, so the three are free to disagree and routinely do.
        """
        ...

    @abstractmethod
    def steps_for(self, phase: ApplyPhase | None = None) -> tuple[ApplyStep, ...]:
        """The steps in the requested phase, in position order.

        :data:`~agentclaw.community.core.bot_config_manifest.apply.order.APPLY_ORDER`
        sorted by position and filtered through this family's
        :meth:`phase_of`. ``None`` means the whole apply, which is what an
        ordinary apply on an existing bot wants; the creation job passes one
        half. This is the callable handed to ``ApplyOrchestrator``.
        """
        ...

    @abstractmethod
    def needs_container(self) -> bool:
        """Whether any construct of this family lands only after the container.

        ``False`` means the creation job may skip waiting for ``ACTIVE`` and
        run no post-container phase.
        """
        ...

    @abstractmethod
    def ports(self) -> MaterialiserPorts:
        """The write targets for this family's materialisers.

        Called per apply, not cached: the callables behind it reach the device
        graph, which is resolved lazily.
        """
        ...

    @abstractmethod
    async def finish(self, ctx: ApplyContext, report: ApplyReport) -> Optional[str]:
        """Close an apply after every category is written.

        Answers a string that becomes one of ``ApplyReport.notes``, or ``None``
        when there is nothing to say. Only the platform-managed teclaw strategy
        has anything to do here: it answers ``None`` on success and the
        redeliver's failure text otherwise. A failure here must **not** raise:
        every category is already written, and losing the report would be worse
        than recording the note.
        """
        ...


#: The container family's phase per construct. ``script`` is the only
#: construct that needs no container; everything else lands after it is up.
_ARCA_PHASES: Mapping[ApplyConstruct, ApplyPhase] = MappingProxyType({
    ManifestSection.SCRIPT: ApplyPhase.PRE_CONTAINER,
    ManifestCategory.IDENTITY: ApplyPhase.ON_CONTAINER,
    ManifestCategory.RESOURCES: ApplyPhase.ON_CONTAINER,
    ManifestCategory.SKILLS: ApplyPhase.ON_CONTAINER,
    ManifestCategory.MCP: ApplyPhase.ON_CONTAINER,
    ManifestCategory.ENGINE_CONFIG: ApplyPhase.ON_CONTAINER,
    ManifestCategory.CLI_TOOLS: ApplyPhase.ON_CONTAINER,
})

#: Platform-managed teclaw: the artifact is the delivery and it is composed
#: before provisioning, so nothing waits for a container. ``script`` is
#: unsupported on this family (the capability resolver refuses it); its phase
#: is stated anyway, and stated as the ARCA one, so a declared script still
#: walks the orchestrator's no-support path and is reported, not skipped.
_TECLAW_PLATFORM_PHASES: Mapping[ApplyConstruct, ApplyPhase] = MappingProxyType({
    construct: ApplyPhase.PRE_CONTAINER for construct in _ARCA_PHASES
})

#: Device-backed teclaw: the ARCA table, with the two constructs that ignore
#: the deployment's mode moved in front of the container.
#:
#: ``cli_tools`` is always platform-managed on this family whatever mode the
#: deployment runs (spec D-6, D-8), exactly as ``mcp`` is, so it is composed
#: into the artifact before provisioning. The distinction is invisible on an
#: existing bot, where the two phases run back to back; it decides something on
#: exactly one path, the W13 creation whose platform-managed sequence has no
#: phase B at all, where an ``ON_CONTAINER`` ``cli_tools`` would never run.
_TECLAW_DEVICE_PHASES: Mapping[ApplyConstruct, ApplyPhase] = MappingProxyType({
    **_ARCA_PHASES,
    ManifestSection.SCRIPT: ApplyPhase.PRE_CONTAINER,
    ManifestCategory.CLI_TOOLS: ApplyPhase.PRE_CONTAINER,
})

# The no-drift assertion ``source_fetchers.FETCHER_TYPES`` and ``fetch/limits``
# set the precedent for: adding a construct to APPLY_ORDER without giving a
# family a phase for it would otherwise be a KeyError on the first apply that
# walked it, on whichever bot happened to declare it first.
for _name, _table in (
    ("_ARCA_PHASES", _ARCA_PHASES),
    ("_TECLAW_PLATFORM_PHASES", _TECLAW_PLATFORM_PHASES),
    ("_TECLAW_DEVICE_PHASES", _TECLAW_DEVICE_PHASES),
):
    assert set(_table) == {step.construct for step in APPLY_ORDER}, (
        f"{_name} and APPLY_ORDER must name the same set of constructs — a "
        "construct in the order with no phase here has no delivery at all"
    )
del _name, _table


def _steps(
    phase_of: Callable[[ApplyStep], ApplyPhase],
    phase: ApplyPhase | None,
) -> tuple[ApplyStep, ...]:
    return tuple(
        step
        for step in sorted(APPLY_ORDER, key=lambda s: s.position)
        if phase is None or phase_of(step) is phase
    )


class _PhaseTableDelivery(DeliveryStrategy):
    """A strategy whose phase answer is a literal table.

    All three are. Stating it once is what makes a family *be* a table plus a
    port bundle — the two halves a reviewer has to check — instead of three
    copies of the same two methods, where the copies are free to drift and the
    drift shows up as a construct silently skipped on one family.

    Still abstract: ``family``, ``creation_sequence``, ``needs_container``,
    ``ports`` and ``finish`` stay unanswered here, so a subclass that forgets
    one cannot be constructed.
    """

    #: The family's phase per construct. Exhaustive over ``APPLY_ORDER``,
    #: asserted at import above.
    _PHASES: ClassVar[Mapping[ApplyConstruct, ApplyPhase]]

    def phase_of(self, step: ApplyStep) -> ApplyPhase:
        return self._PHASES[step.construct]

    def steps_for(self, phase: ApplyPhase | None = None) -> tuple[ApplyStep, ...]:
        return _steps(self.phase_of, phase)


class ArcaDelivery(_PhaseTableDelivery):
    """The container family: the phase table is :data:`_ARCA_PHASES`.

    ``script`` before the container, every other construct after it;
    ``needs_container`` is ``True``, and ``finish`` has nothing to do because
    the owning services project as they write.
    """

    family = EngineFamily.ARCA
    creation_sequence = CreationSequence.CREATE_BETWEEN_PHASES
    _PHASES = _ARCA_PHASES

    def __init__(self, ports: Callable[[], MaterialiserPorts]) -> None:
        self._ports = ports

    def needs_container(self) -> bool:
        return True

    def ports(self) -> MaterialiserPorts:
        return self._ports()

    async def finish(self, ctx: ApplyContext, report: ApplyReport) -> Optional[str]:
        # The owning services project as they write (device writes land,
        # activation reconciles); there is nothing left to close.
        return None


#: The closing step for a platform-managed teclaw apply. Takes the apply's
#: context and answers ``None`` on success, or a report-safe note on failure::
#:
#:     async def redeliver(ctx: ApplyContext) -> Optional[str]: ...
#:
#: One whole-artifact redeliver to the running container, or nothing at all
#: when the bot has no live binding — on the creation path, provisioning
#: composes the first artifact instead, so there is nothing to redeliver to.
#: The note it answers becomes one of ``ApplyReport.notes``.
#:
#: Implemented by: ``apply/redeliver``. Consumed by:
#: :meth:`TeclawPlatformDelivery.finish`.
Redeliver = Callable[[ApplyContext], Awaitable[Optional[str]]]


class TeclawPlatformDelivery(_PhaseTableDelivery):
    """The artifact family, delivering through platform state (spec D-3).

    Every construct is ``PRE_CONTAINER``: it writes a database row or an
    object into the bot-data store, and the artifact composed from that state
    is the delivery. Nothing waits for a container, so the creation job needs
    no phase B and no ``ACTIVE`` wait, and the apply is closed by one
    whole-artifact redeliver.

    It has no CLI-port substitution to make, unlike its device-backed sibling:
    its bundle is built teclaw-side already, by the same composition root
    provider that binds the store these ports write to.
    """

    family = EngineFamily.TECLAW
    creation_sequence = CreationSequence.RECORD_APPLY_PROVISION
    _PHASES = _TECLAW_PLATFORM_PHASES

    def __init__(
        self,
        *,
        ports: Callable[[], MaterialiserPorts],
        redeliver: Redeliver,
    ) -> None:
        self._ports = ports
        # Required, and it is the constructor that makes it so. The old shape
        # bound both port bundles and a switch, which let "platform-managed
        # with nothing bound to be platform-managed *with*" be expressible at
        # all — a misconfiguration the factory then had to refuse at run time,
        # loudly, in front of a bot. A strategy that cannot be built without
        # its own collaborators has no such state to refuse.
        self._redeliver = redeliver

    def needs_container(self) -> bool:
        return False

    def ports(self) -> MaterialiserPorts:
        return self._ports()

    async def finish(self, ctx: ApplyContext, report: ApplyReport) -> Optional[str]:
        return await self._redeliver(ctx)


class TeclawDeviceDelivery(_PhaseTableDelivery):
    """The artifact family, delivering the way it did before W8.

    Every non-script construct is ``ON_CONTAINER`` through the device-backed
    ports ARCA uses, so nothing regresses in a deployment whose engine has not
    shipped the artifact's ``ownership`` map yet. This class is the whole of
    that compatibility: it goes when :class:`TeclawDeliveryMode` does.

    **The one thing it does not take from ARCA is the CLI port.** ``cli_tools``
    is always platform-managed on this family, so its port cannot come from the
    device bundle: that bundle carries the *ARCA* port, which would call
    ARCA-only engine endpoints on a teclaw bot — and, since
    :data:`_TECLAW_DEVICE_PHASES` puts this category before the container,
    would run with no container to call at all. The substitution is here, in
    the one strategy that hands out a bundle it did not have built for it,
    rather than in a second wiring site that would have to agree with the
    first.

    ``mcp`` is always platform-managed too and needs none of this, because it
    has no port in :class:`MaterialiserPorts` at all: on both families its
    delivery *is* the artifact, so there is nothing family-specific to select.
    ``cli_tools`` is the one category that is always platform-managed and still
    has a per-family delivery step — ARCA installs into a live container over
    an engine endpoint, teclaw does nothing — so it owns a port, and a port
    taken from the wrong family's bundle is the exact mismatch corrected here.
    """

    family = EngineFamily.TECLAW
    creation_sequence = CreationSequence.CREATE_BETWEEN_PHASES
    _PHASES = _TECLAW_DEVICE_PHASES

    def __init__(
        self,
        *,
        ports: Callable[[], MaterialiserPorts],
        cli_tool_service: Callable[[], CliToolService],
    ) -> None:
        self._ports = ports
        # Lazy for the reason the bundle itself is lazy — it reaches the device
        # graph — and resolved per apply, not once at boot.
        self._cli_tool_service = cli_tool_service

    def needs_container(self) -> bool:
        return True

    def ports(self) -> MaterialiserPorts:
        return replace(self._ports(), cli_tool_service=self._cli_tool_service())

    async def finish(self, ctx: ApplyContext, report: ApplyReport) -> Optional[str]:
        # This shape runs the ARCA one: the device-backed ports projected as
        # they wrote, so there is nothing left to close.
        return None


@dataclass(frozen=True)
class TeclawPlatformBindings:
    """What the platform-managed teclaw path needs bound, as one DI value.

    Two fields, both callables, so nothing is resolved until an apply actually
    runs on a teclaw bot::

        TeclawPlatformBindings(
            platform_ports=lambda: MaterialiserPorts(...),  # store-backed
            redeliver=<an async (ApplyContext) -> Optional[str]>,
        )

    Created by: the composition root, in the manifest-fetch graph beside the
    store these ports write to.
    Consumed by: :class:`TeclawDeliveryBindings`, and the W9 CLI service
    factory, whose ``teclaw-live`` binding pushes through the same redeliver.
    """

    #: Builds the store-backed port bundle. Lazy: it reaches the object store
    #: graph.
    platform_ports: Callable[[], MaterialiserPorts]
    #: The closing whole-artifact redeliver. See :data:`Redeliver`.
    redeliver: Redeliver


@dataclass(frozen=True)
class TeclawDeliveryBindings:
    """Everything either teclaw strategy is built from, as one DI value.

    Four fields, of which each row of :data:`TECLAW_DELIVERY_BY_MODE` reads the
    two its own mode needs and never touches the other two. They share one
    bundle so the rows share one signature — that is what lets the selection be
    a table lookup instead of a branch that knows what each arm wants. Every
    field is a callable, so binding all four resolves nothing: only the
    strategy the mode names is built, and even that one reaches its graph no
    earlier than the first apply.

    Created by: the composition root.
    Consumed by: :func:`teclaw_delivery_for_mode`.
    """

    #: Store-backed ports — ``PLATFORM``'s write targets.
    platform_ports: Callable[[], MaterialiserPorts]
    #: Device-backed ports, the bundle ARCA also runs on — ``DEVICE``'s.
    device_ports: Callable[[], MaterialiserPorts]
    #: ``PLATFORM``'s closing step. See :data:`Redeliver`.
    redeliver: Redeliver
    #: The teclaw-bound CLI service ``DEVICE`` substitutes into its bundle.
    cli_tool_service: Callable[[], CliToolService]


#: Which teclaw strategy a deployment's mode names, and how it is built.
#:
#: One row per :class:`TeclawDeliveryMode`, exhaustive by construction (the
#: import-time check below). Supporting a third shape is a class and a row, the
#: way ``source_fetchers.FETCHER_TYPES`` takes a protocol; retiring the
#: device-backed shape is deleting a row, a class and an enum member, and
#: nothing else in the feature has to be found and unbranched first.
TECLAW_DELIVERY_BY_MODE: Mapping[
    TeclawDeliveryMode, Callable[[TeclawDeliveryBindings], DeliveryStrategy]
] = MappingProxyType({
    TeclawDeliveryMode.PLATFORM: lambda bindings: TeclawPlatformDelivery(
        ports=bindings.platform_ports, redeliver=bindings.redeliver
    ),
    TeclawDeliveryMode.DEVICE: lambda bindings: TeclawDeviceDelivery(
        ports=bindings.device_ports, cli_tool_service=bindings.cli_tool_service
    ),
})

_UNBUILT_MODES = set(TeclawDeliveryMode) - set(TECLAW_DELIVERY_BY_MODE)
if _UNBUILT_MODES:
    # At import, never at boot-and-then-first-apply. A mode a deployment can
    # write into its yaml and nothing can build is "the surface accepts what it
    # cannot run" — the rule this whole feature is built around.
    raise RuntimeError(
        "every teclaw delivery mode needs a strategy; missing: "
        + ", ".join(sorted(m.value for m in _UNBUILT_MODES))
    )


def teclaw_delivery_for_mode(
    mode: TeclawDeliveryMode, bindings: TeclawDeliveryBindings
) -> DeliveryStrategy:
    """The one teclaw strategy this deployment runs.

    Called once, by the composition root::

        teclaw_delivery_for_mode(config.teclaw_delivery_mode, bindings)

    The other mode's strategy is never built, so nothing downstream can be
    handed it by accident and nothing has to ask which mode it is holding.
    """
    return TECLAW_DELIVERY_BY_MODE[mode](bindings)


def family_from_engine_test(
    is_teclaw: Callable[[Optional[str]], bool],
) -> Callable[[Optional[str]], EngineFamily]:
    """Adapt the engine authority's yes/no into the family key.

    ``TeclawProvisionService.is_teclaw`` answers a boolean because that is the
    question the rest of the platform asks it. :class:`DeliveryStrategyFactory`
    keys by :class:`EngineFamily`, so the translation happens once, here, at
    the boundary with the authority — not at each of the places that would
    otherwise write ``TECLAW if is_teclaw(e) else ARCA``.

    Called by: the composition root, wrapping the same authority the capability
    resolver and the CLI-tool surface take, so the three cannot disagree about
    what a bot is.
    """
    families: Mapping[bool, EngineFamily] = MappingProxyType({
        True: EngineFamily.TECLAW,
        False: EngineFamily.ARCA,
    })
    return lambda engine_type: families[bool(is_teclaw(engine_type))]


class DeliveryStrategyFactory:
    """Which of the bound strategies a bot applies through. A lookup, and only that.

    It is handed one built strategy per :class:`EngineFamily` and a way to name
    a bot's family::

        DeliveryStrategyFactory(
            family_of=family_from_engine_test(provisioning.is_teclaw),
            strategies={
                EngineFamily.ARCA: ArcaDelivery(device_ports),
                EngineFamily.TECLAW: teclaw_delivery_for_mode(mode, bindings),
            },
        )

    It builds nothing and configures nothing. Which teclaw shape sits in that
    second row was settled in the composition root, by
    :data:`TECLAW_DELIVERY_BY_MODE`; what reaches this class is an object that
    already knows how it delivers. So there is no switch to read here, no
    collaborator to pass through to a constructor, and no misconfiguration left
    to refuse — a strategy that could not be built was not bound, and a family
    with no strategy fails below, at boot.

    Created by: the composition root.
    Consumed by: the apply service, which asks it per apply and per bot.
    """

    def __init__(
        self,
        *,
        family_of: Callable[[Optional[str]], EngineFamily],
        strategies: Mapping[EngineFamily, DeliveryStrategy],
    ) -> None:
        unserved = set(EngineFamily) - set(strategies)
        if unserved:
            # The same exhaustiveness the phase tables and the mode table get,
            # at the one point where the mapping is assembled rather than
            # written down: a family with no strategy would otherwise be a
            # KeyError on the first apply of a bot that happened to be one.
            raise ValueError(
                "every engine family needs a delivery strategy; missing: "
                + ", ".join(sorted(f.value for f in unserved))
            )
        self._family_of = family_of
        self._strategies = MappingProxyType(dict(strategies))

    def for_family(self, family: EngineFamily) -> DeliveryStrategy:
        """The strategy bound for a family, named directly."""
        return self._strategies[family]

    def for_engine(self, engine_type: Optional[str]) -> DeliveryStrategy:
        """The strategy a bot of this engine type applies through."""
        return self._strategies[self._family_of(engine_type)]

    def for_bot(self, bot: Mapping[str, Any]) -> DeliveryStrategy:
        return self.for_engine(bot.get("active_engine"))


__all__ = [
    "ArcaDelivery",
    "CreationSequence",
    "DeliveryStrategy",
    "DeliveryStrategyFactory",
    "EngineFamily",
    "MaterialiserPorts",
    "Redeliver",
    "TECLAW_DELIVERY_BY_MODE",
    "TECLAW_PLATFORM_MANAGED_KEY",
    "TeclawDeliveryBindings",
    "TeclawDeliveryMode",
    "TeclawDeviceDelivery",
    "TeclawPlatformBindings",
    "TeclawPlatformDelivery",
    "family_from_engine_test",
    "teclaw_delivery_for_mode",
    "teclaw_delivery_mode_from_config",
]
