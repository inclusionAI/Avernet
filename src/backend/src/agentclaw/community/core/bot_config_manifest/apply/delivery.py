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

**The switch.** Until the teclaw engine supports the artifact's ``ownership``
map, the platform-managed path is behind
``user_config.bot_config_manifest.teclaw_platform_managed`` (default off). Off,
teclaw runs the shape it ran before W8: every non-script construct after the
container, through the same device-backed ports ARCA uses. The switch is read
here, by the factory, and nowhere else.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol

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
    ALL_PHASES,
    APPLY_ORDER,
    ApplyPhase,
    ApplyStep,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyConstruct,
    ApplyReport,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestSection

#: The yaml key under ``user_config.bot_config_manifest``::
#:
#:     user_config:
#:       bot_config_manifest:
#:         teclaw_platform_managed: true
TECLAW_PLATFORM_MANAGED_KEY = "teclaw_platform_managed"


class CreationSequence(StrEnum):
    """The order a W13 creation runs its steps in, per family.

    Each value names its steps in the order they run; the two differ in
    *where the container is created* relative to the manifest phases.
    """

    #: pre-container phase → create the bot **and provision it** → wait for
    #: ``ACTIVE`` → post-container phase. The manifest is applied in two
    #: halves around the container. ARCA's sequence, and teclaw's while the
    #: platform-managed switch is off.
    CREATE_BETWEEN_PHASES = "create_between_phases"
    #: create the bot **record only** → the single pre-container phase writes
    #: platform state against it → provision (which composes the first
    #: artifact from that state) → wait for ``ACTIVE``. No post-container
    #: phase: everything was delivered before the container existed. teclaw
    #: with the platform-managed switch on.
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

    Created by: ``ArcaDelivery.ports`` and ``TeclawDelivery.ports``, each
    delegating to a lazy callable bound by the composition root.
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

    The four things it owns, and how the two implementations answer:

    ======================  =====================  ========================
    Member                  ``ArcaDelivery``       ``TeclawDelivery``
                                                   (platform-managed on)
    ======================  =====================  ========================
    ``family``              ``"arca"``             ``"teclaw"``
    ``creation_sequence``   ``CREATE_BETWEEN_``    ``RECORD_APPLY_``
                            ``PHASES``             ``PROVISION``
    ``phase_of(step)``      :data:`_ARCA_PHASES`   ``PRE_CONTAINER`` for
                                                   every construct
    ``needs_container()``   ``True``               ``False``
    ``ports()``             device-backed          store-backed
    ``finish()``            ``None``               one whole-artifact
                                                   redeliver
    ======================  =====================  ========================

    With the switch **off**, ``TeclawDelivery`` answers the ARCA column for
    every row except ``family`` and ``ports`` — that is the point of the
    switch.

    Created by: ``DeliveryStrategyFactory.for_engine`` / ``for_bot``.
    Consumed by: the apply service (``ports``, ``finish``), the orchestrator
    (``steps_for``), and the creation job (``creation_sequence``,
    ``needs_container``).

    Implemented by ``ArcaDelivery`` and ``TeclawDelivery`` below, which
    subclass it explicitly so the implementations are one jump away.
    """

    @property
    def family(self) -> str:
        """The engine family's name: ``"arca"`` or ``"teclaw"``.

        The key the factory selects a strategy by and the word a report or a
        log uses for it. Example: ``ArcaDelivery().family == "arca"``;
        ``DeliveryStrategyFactory.for_engine("teclaw").family == "teclaw"``.
        """
        ...

    @property
    def creation_sequence(self) -> CreationSequence:
        """Which of the two creation orders this family runs."""
        ...

    def phase_of(self, step: ApplyStep) -> ApplyPhase:
        """Which phase this family delivers the step's construct in.

        The phase table is the strategy's own — :data:`ApplyStep` carries no
        phase, so the two families are free to disagree and routinely do.
        """
        ...

    def steps_for(
        self, phases: frozenset[ApplyPhase] | None = None
    ) -> tuple[ApplyStep, ...]:
        """The steps in the requested phases, in position order.

        :data:`~agentclaw.community.core.bot_config_manifest.apply.order.APPLY_ORDER`
        sorted by position and filtered through this family's
        :meth:`phase_of`. ``None`` means both phases, which is what an ordinary
        apply on an existing bot wants; the creation job passes one at a time.
        This is the callable handed to ``ApplyOrchestrator``.
        """
        ...

    def needs_container(self) -> bool:
        """Whether any construct of this family lands only after the container.

        ``False`` means the creation job may skip waiting for ``ACTIVE`` and
        run no post-container phase.
        """
        ...

    def ports(self) -> MaterialiserPorts:
        """The write targets for this family's materialisers.

        Called per apply, not cached: the callables behind it reach the device
        graph, which is resolved lazily.
        """
        ...

    async def finish(self, ctx: ApplyContext, report: ApplyReport) -> Optional[str]:
        """Close an apply after every category is written.

        Answers a string that becomes one of ``ApplyReport.notes``, or ``None``
        when there is nothing to say. ARCA always answers ``None``; teclaw
        answers ``None`` on success and the redeliver's failure text otherwise.
        A failure here must **not** raise: every category is already written,
        and losing the report would be worse than recording the note.
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

# The no-drift assertion ``source_fetchers.FETCHER_TYPES`` and ``fetch/limits``
# set the precedent for: adding a construct to APPLY_ORDER without giving this
# family a phase for it would otherwise be a KeyError on the first apply that
# walked it, on whichever bot happened to declare it first.
assert set(_ARCA_PHASES) == {step.construct for step in APPLY_ORDER}, (
    "_ARCA_PHASES and APPLY_ORDER must name the same set of constructs — a "
    "construct in the order with no phase here has no ARCA delivery at all"
)


def _steps(
    phase_of: Callable[[ApplyStep], ApplyPhase],
    phases: frozenset[ApplyPhase] | None,
) -> tuple[ApplyStep, ...]:
    wanted = ALL_PHASES if phases is None else phases
    return tuple(
        step
        for step in sorted(APPLY_ORDER, key=lambda s: s.position)
        if phase_of(step) in wanted
    )


class ArcaDelivery(DeliveryStrategy):
    """The container family: the phase table is :data:`_ARCA_PHASES`.

    ``script`` before the container, every other construct after it;
    ``needs_container`` is ``True``, and ``finish`` has nothing to do because
    the owning services project as they write.
    """

    family = "arca"
    creation_sequence = CreationSequence.CREATE_BETWEEN_PHASES

    def __init__(self, ports: Callable[[], MaterialiserPorts]) -> None:
        self._ports = ports

    def phase_of(self, step: ApplyStep) -> ApplyPhase:
        return _ARCA_PHASES[step.construct]

    def steps_for(
        self, phases: frozenset[ApplyPhase] | None = None
    ) -> tuple[ApplyStep, ...]:
        return _steps(self.phase_of, phases)

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
#: :meth:`TeclawDelivery.finish`.
Redeliver = Callable[[ApplyContext], Awaitable[Optional[str]]]


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
    Consumed by: the apply service, which unpacks it into
    ``DeliveryStrategyFactory``.
    """

    #: Builds the store-backed port bundle. Lazy: it reaches the object store
    #: graph.
    platform_ports: Callable[[], MaterialiserPorts]
    #: The closing whole-artifact redeliver. See :data:`Redeliver`.
    redeliver: Redeliver


class TeclawDelivery(DeliveryStrategy):
    """The artifact family.

    Its phase table is not a literal like :data:`_ARCA_PHASES` but computed in
    :meth:`phase_of` from the platform-managed switch plus the two per-construct
    rules that ignore it (``script`` and ``cli_tools``).

    With ``platform_managed`` on, every non-script construct is
    ``PRE_CONTAINER``: it writes platform state and needs no container. The
    apply is closed by one redeliver. Off, the strategy reproduces the shape
    teclaw ran before W8 — every non-script construct ``ON_CONTAINER`` through
    the device-backed ports — so nothing regresses while the engine catches up.
    """

    family = "teclaw"

    def __init__(
        self,
        *,
        platform_managed: bool,
        platform_ports: Callable[[], MaterialiserPorts],
        device_ports: Callable[[], MaterialiserPorts],
        redeliver: Optional[Redeliver] = None,
        cli_tool_service: Optional[CliToolService] = None,
    ) -> None:
        self._platform_managed = platform_managed
        self._platform_ports = platform_ports
        self._device_ports = device_ports
        # W9. ``cli_tools`` is always platform-managed on this family, so its
        # port cannot be whichever the switch selects: with the switch off the
        # device bundle carries the *ARCA* port, which would call ARCA-only
        # engine endpoints on a teclaw bot — and, since ``phase_of`` puts this
        # category before the container, would run with no container to call at
        # all. Substituted into whichever bundle ``ports`` returns, so the
        # invariant holds in one place instead of depending on two wiring sites
        # agreeing.
        #
        # ``mcp`` is always platform-managed too and needs none of this,
        # because it has no port in ``MaterialiserPorts`` at all: on both
        # families its delivery *is* the artifact, so there is nothing
        # family-specific to select. ``cli_tools`` is the one category that is
        # always platform-managed and still has a per-family delivery step —
        # ARCA installs into a live container over an engine endpoint, teclaw
        # does nothing — so it owns a port, and a port selected by a switch
        # this category ignores is the exact mismatch corrected here.
        self._cli_tool_service = cli_tool_service
        self._redeliver = redeliver

    @property
    def platform_managed(self) -> bool:
        return self._platform_managed

    @property
    def creation_sequence(self) -> CreationSequence:
        if self._platform_managed:
            return CreationSequence.RECORD_APPLY_PROVISION
        return CreationSequence.CREATE_BETWEEN_PHASES

    def phase_of(self, step: ApplyStep) -> ApplyPhase:
        if step.construct == ManifestSection.SCRIPT:
            # Unsupported on teclaw (the capability resolver refuses it); the
            # phase is stated anyway, and stated as the ARCA one, so a declared
            # script still walks the orchestrator's no-support path and is
            # reported, not skipped.
            return ApplyPhase.PRE_CONTAINER
        if step.construct == ManifestCategory.CLI_TOOLS:
            # The artifact is teclaw's delivery and it is composed before
            # provisioning, so this category is PRE_CONTAINER whatever the
            # switch says. It has to be stated per category rather than left to
            # the generic re-phasing below, because that keys on the switch and
            # this one is always platform-managed — like ``mcp``, and for the
            # same reason (spec D-6, D-8). The distinction is invisible on an
            # existing bot, where the two phases run back to back; it decides
            # something on exactly one path, the W13 creation whose
            # switch-on sequence has no phase B at all.
            return ApplyPhase.PRE_CONTAINER
        if self._platform_managed:
            return ApplyPhase.PRE_CONTAINER
        return ApplyPhase.ON_CONTAINER

    def steps_for(
        self, phases: frozenset[ApplyPhase] | None = None
    ) -> tuple[ApplyStep, ...]:
        return _steps(self.phase_of, phases)

    def needs_container(self) -> bool:
        return not self._platform_managed

    def ports(self) -> MaterialiserPorts:
        bundle = (
            self._platform_ports() if self._platform_managed else self._device_ports()
        )
        if self._cli_tool_service is None:
            return bundle
        return replace(bundle, cli_tool_service=self._cli_tool_service)

    async def finish(self, ctx: ApplyContext, report: ApplyReport) -> Optional[str]:
        if not self._platform_managed or self._redeliver is None:
            return None
        return await self._redeliver(ctx)


_TRUE_SCALARS = frozenset({"true", "yes", "on", "1"})
_FALSE_SCALARS = frozenset({"false", "no", "off", "0"})


def teclaw_platform_managed_from_config(tree: Mapping[str, Any] | None) -> bool:
    """The switch, read from the ``user_config`` tree. Absent is off.

    ``tree`` is the merged ``user_config`` mapping; the block this reads is
    ``tree["bot_config_manifest"][TECLAW_PLATFORM_MANAGED_KEY]``.

    ============================================  =====================
    Value at that key                             Result
    ============================================  =====================
    absent, or the block is missing/not a         ``False``
    mapping, or ``tree`` is ``None``
    ``None`` (``teclaw_platform_managed:``        ``False``
    with nothing after it)
    ``True`` / ``False``                          as written
    ``"true"``, ``"yes"``, ``"on"``, ``"1"``      ``True``
    ``"false"``, ``"no"``, ``"off"``, ``"0"``     ``False``
    ``0`` / ``1``                                 ``False`` / ``True``
    anything else                                 raises ``ValueError``
    ============================================  =====================

    Called by: the composition root, at boot.

    Strict, and the strictness is the point: YAML may hand back a string, and
    ``bool("false")`` is ``True``. A switch that turns a delivery path on
    because someone quoted ``"off"`` would fail every teclaw apply in a
    deployment whose engine has not shipped the map. So only a boolean, the
    usual boolean spellings, or 0/1 are accepted; anything else raises at
    boot, where a config mistake belongs. A block that is not a mapping is
    read as absent, the way the sibling readers treat a missing block.
    """
    block = (tree or {}).get("bot_config_manifest") or {}
    if not isinstance(block, Mapping) or TECLAW_PLATFORM_MANAGED_KEY not in block:
        return False
    raw = block[TECLAW_PLATFORM_MANAGED_KEY]
    if raw is None:
        # ``teclaw_platform_managed:`` with nothing after it — the likeliest
        # spelling of "not set" — reads as absent, not as a malformed value.
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in _TRUE_SCALARS:
            return True
        if text in _FALSE_SCALARS:
            return False
        raise ValueError(
            f"user_config.bot_config_manifest.{TECLAW_PLATFORM_MANAGED_KEY}: "
            f"not a boolean: {raw!r}"
        )
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    raise ValueError(
        f"user_config.bot_config_manifest.{TECLAW_PLATFORM_MANAGED_KEY}: "
        f"not a boolean: {raw!r}"
    )


class DeliveryStrategyFactory:
    """Pick the strategy for a bot. The one reader of the switch.

    ``for_engine("claude_code")`` answers an :class:`ArcaDelivery`;
    ``for_engine("teclaw")`` answers a :class:`TeclawDelivery` carrying the
    switch. ``for_bot(bot)`` is the same thing keyed off
    ``bot["active_engine"]``.

    Raises ``RuntimeError`` when the switch is on and no platform ports are
    bound: a misconfiguration should be loud, not a quiet fallback to writing
    into a container through the device ports.
    """

    def __init__(
        self,
        *,
        is_teclaw: Callable[[Optional[str]], bool],
        teclaw_platform_managed: bool,
        arca_ports: Callable[[], MaterialiserPorts],
        teclaw_platform_ports: Optional[Callable[[], MaterialiserPorts]] = None,
        redeliver: Optional[Redeliver] = None,
        teclaw_cli_tool_service: Optional[Callable[[], CliToolService]] = None,
    ) -> None:
        self._is_teclaw = is_teclaw
        self._platform_managed = teclaw_platform_managed
        self._arca_ports = arca_ports
        # The platform-managed path needs its own ports. With the switch on and
        # none bound, ``for_engine`` refuses rather than silently writing into
        # a container through the device ports — a misconfiguration should be
        # loud, not a quiet fallback.
        self._teclaw_platform_ports = teclaw_platform_ports
        self._redeliver = redeliver
        # W9: the teclaw-bound CLI service, handed to every teclaw strategy
        # whatever the switch says. A lazy callable for the reason every other
        # port here is lazy — it reaches the device graph.
        self._teclaw_cli_tool_service = teclaw_cli_tool_service

    @property
    def teclaw_platform_managed(self) -> bool:
        return self._platform_managed

    def for_engine(self, engine_type: Optional[str]) -> DeliveryStrategy:
        if not self._is_teclaw(engine_type):
            return ArcaDelivery(self._arca_ports)
        platform_managed = self._platform_managed
        if platform_managed and self._teclaw_platform_ports is None:
            raise RuntimeError(
                "teclaw_platform_managed is on but no platform ports are bound"
            )
        return TeclawDelivery(
            platform_managed=platform_managed,
            # With the switch off the platform ports are never consulted;
            # ``_arca_ports`` stands in only so the constructor has a callable.
            platform_ports=self._teclaw_platform_ports or self._arca_ports,
            device_ports=self._arca_ports,
            redeliver=self._redeliver,
            cli_tool_service=(
                self._teclaw_cli_tool_service()
                if self._teclaw_cli_tool_service is not None
                else None
            ),
        )

    def for_bot(self, bot: Mapping[str, Any]) -> DeliveryStrategy:
        return self.for_engine(bot.get("active_engine"))


__all__ = [
    "ArcaDelivery",
    "CreationSequence",
    "DeliveryStrategy",
    "DeliveryStrategyFactory",
    "MaterialiserPorts",
    "Redeliver",
    "TECLAW_PLATFORM_MANAGED_KEY",
    "TeclawDelivery",
    "TeclawPlatformBindings",
    "teclaw_platform_managed_from_config",
]
