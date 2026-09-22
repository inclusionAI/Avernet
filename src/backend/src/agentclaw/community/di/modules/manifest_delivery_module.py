"""ManifestDeliveryModule — how a manifest reaches a bot, wired (W8).

The delivery seam's half of the manifest graph: the two port bundles a
materialiser can be handed, one provider per **bound strategy**, and the
family lookup the apply service asks per bot. It moved out of
``manifest_fetch_module`` because that file is near the 1000-line cap and
because the cluster reads as one thing: everything here exists so that
``DeliveryStrategyFactory`` can be built by asking for its two arguments
rather than by assembling them.

**One provider per component, including the ones only another provider
wants.** The device-backed bundle, the store-backed bundle, the closing
redeliver, each strategy and the family adapter are all bindings of their own,
so no provider in this file constructs a collaborator for another provider to
consume, and none reaches into a value to get at a field of it. The two port
bundles are the same ``Callable[[], MaterialiserPorts]`` shape, so they are told
apart the way this codebase tells one ``HttpClient`` from another: by qualifier
(:data:`DevicePorts` / :data:`PlatformPorts`). What
``manifest_delivery_strategies`` does is exactly what
:class:`DeliveryStrategyFactory` does with what it is given: nothing but a
lookup.

**The teclaw mode is two providers, not a switch.** A deployment runs one of
two teclaw shapes (:class:`TeclawDeliveryMode`), and each has a provider that
contributes its strategy to the family map only when the deployment named its
own mode, an empty mapping otherwise. So no strategy is handed a mode to branch
on and no provider assembles the shape it selects: it takes the bound one.

Both shapes are *bound* on either mode — a binding is resolvable by definition,
and ``discover_lifecycle_participants`` constructs every one of them at startup
anyway — so what the mode decides is which of them the family map carries, and
therefore which one an apply can reach. Nothing should resolve a strategy class
by name: :class:`DeliveryStrategyFactory` is the seam, and its map holds the one
row per family that the deployment actually runs.

The two rows contribute to one binding — :data:`DeliveryStrategies` — because
injector merges a map multibinding across providers, which is what makes "one
provider per strategy" something a single mapping-valued binding can be
assembled out of at all.

Coverage — every mode naming a strategy, which the selection table this
replaced checked at import — is asserted over the real graph, for every member
of the enum, by ``tests/community/di/test_manifest_delivery_module_wiring.py``.
"""
from __future__ import annotations

from typing import Callable

from injector import Injector, Module, inject, multiprovider, provider, singleton

from agentclaw.community.core.bot_config_manifest.apply.activation_delegates import (
    DeviceActivation,
    PlatformActivation,
)
from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    ArcaDelivery,
    DeliveryStrategies,
    DeliveryStrategyFactory,
    DevicePorts,
    EngineFamily,
    EngineFamilyOf,
    MaterialiserPorts,
    PlatformPorts,
    Redeliver,
    TeclawDeliveryMode,
    TeclawDeviceDelivery,
    TeclawPlatformDelivery,
    family_from_engine_test,
)
from agentclaw.community.core.bot_config_manifest.apply.identity_files import (
    DeviceIdentity,
)
from agentclaw.community.core.bot_config_manifest.apply.redeliver import TeclawRedeliver
from agentclaw.community.core.bot_config_manifest.apply.resource_files import (
    DeviceResource,
)
from agentclaw.community.core.bot_config_manifest.apply.skill_package_upload import (
    DeviceSkillPackageUpload,
)
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    DeclaredSourceResolver,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    CliToolServiceFactory,
)
from agentclaw.community.core.bot_config_manifest.managed_files import (
    ManagedFilesStore,
)
from agentclaw.community.core.bot_config_manifest.managed_files.ports import (
    PlatformIdentity,
    PlatformResource,
    PlatformSkillPackageUpload,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_startup_script.protocols import (
    TeclawEngineTestProtocol,
)
from agentclaw.community.core.mcp.mcp_auth_service_protocol import MCPAuthServiceProtocol
from agentclaw.community.core.ports.identity_file_port import IdentityFilePort
from agentclaw.community.core.ports.resource_file_port import ResourceFilePort
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.di import config as cfg


class ManifestDeliveryModule(Module):
    """Wire the strategy each engine family delivers a manifest through."""

    # ── the two port bundles, and the closing step ─────────────────────────

    @singleton
    @provider
    @inject
    def manifest_device_ports(
        self,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        activation_service_provider: Callable[[], DirectActivationServiceProtocol],
        mcp_auth_service_provider: Callable[[], MCPAuthServiceProtocol],
        identity_service_provider: Callable[[], IdentityFilePort],
        upload_service_provider: Callable[[], LocalSkillUploadServiceProtocol],
        capability_reader_provider: Callable[[], BotCapabilityStateReaderProtocol],
        package_validator_provider: Callable[[], SkillPackageValidator],
        entry_fetcher_provider: Callable[[], DeclaredSourceResolver],
        resource_service_provider: Callable[[], ResourceFilePort],
        cli_tool_service_factory: CliToolServiceFactory,
    ) -> DevicePorts:
        """The device-backed ports — ARCA's writes, and the pre-W8 teclaw shape's.

        The sibling of the store-backed bundle below, and built the same way:
        one bundle, in the composition root, where the delegates that wrap each
        service into its narrow port belong. It was previously assembled inside
        the apply service, out of ten lazy providers that service held for no
        other purpose — a wiring decision made by a component rather than by
        the root that wires it.

        A thunk rather than a value, so what the qualifier names is the
        *unbuilt* bundle. Building one walks the device graph — activation to
        the skill-query service to the device context resolver to
        ``DeviceService``, which reads the workspace config — and that walk must
        not happen while the app is still wiring: ``discover_lifecycle_participants``
        resolves **every** binding at startup to find the ``Lifecycle``
        implementors, and swallows whatever raises. A bundle bound as a value
        would be constructed there, in that walk, on every boot, with any
        failure silently skipped. Bound as a thunk, that same walk gets the
        closure, calls nothing, and moves on.

        The apply service calls it once per apply. That the wrappers are rebuilt
        each time is incidental rather than required — the services behind them
        are singletons and none of them holds per-apply state — but it is the
        cheapest deferral that keeps the graph out of boot, and it means no two
        applies share a bundle.
        """
        def device_ports() -> MaterialiserPorts:
            return MaterialiserPorts(
                script_service=script_service_provider(),
                activation_service=DeviceActivation(activation_service_provider()),
                mcp_auth_service=mcp_auth_service_provider(),
                identity_service=DeviceIdentity(identity_service_provider()),
                upload_service=DeviceSkillPackageUpload(upload_service_provider()),
                capability_reader=capability_reader_provider(),
                package_validator=package_validator_provider(),
                entry_fetcher=entry_fetcher_provider(),
                resource_service=DeviceResource(resource_service_provider()),
                cli_tool_service=cli_tool_service_factory("arca"),
            )

        return device_ports

    @singleton
    @provider
    @inject
    def manifest_platform_ports(
        self,
        injector: Injector,
        store: ManagedFilesStore,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        activation_service_provider: Callable[[], DirectActivationServiceProtocol],
        mcp_auth_service_provider: Callable[[], MCPAuthServiceProtocol],
        capability_reader_provider: Callable[[], BotCapabilityStateReaderProtocol],
        package_validator_provider: Callable[[], SkillPackageValidator],
        entry_fetcher_provider: Callable[[], DeclaredSourceResolver],
        cli_tool_service_factory: CliToolServiceFactory,
    ) -> PlatformPorts:
        """The store-backed ports (W8, spec D-7).

        The three file categories write to the managed-files store instead of
        a container; activation records without projecting. Everything the
        family shares with ARCA — the script service, the permission check,
        the capability reader, the validator, the fetch pipeline — is the
        same object ARCA's ports carry.

        Read by the platform-managed strategy's provider and by nothing else,
        so a ``DEVICE`` deployment builds this thunk and never calls it.
        """
        def platform_ports() -> MaterialiserPorts:
            validator = package_validator_provider()
            return MaterialiserPorts(
                script_service=script_service_provider(),
                activation_service=PlatformActivation(activation_service_provider()),
                mcp_auth_service=mcp_auth_service_provider(),
                identity_service=PlatformIdentity(store),
                upload_service=PlatformSkillPackageUpload(
                    store,
                    validator=validator,
                    skill_repository=injector.get(SkillRepository),
                ),
                capability_reader=capability_reader_provider(),
                package_validator=validator,
                entry_fetcher=entry_fetcher_provider(),
                resource_service=PlatformResource(store),
                # W9 is always platform-managed and never consults the switch,
                # as ``mcp`` does not: the artifact is the delivery on this
                # family whatever the switch says.
                cli_tool_service=cli_tool_service_factory("teclaw"),
            )

        return platform_ports

    @singleton
    @provider
    @inject
    def manifest_teclaw_redeliver(self, injector: Injector) -> Redeliver:
        """The closing whole-artifact push: one per apply, at the end (W8, D-7).

        Bound whichever mode a deployment runs, because it has two consumers
        that do not agree about the mode: the platform-managed strategy closes
        an apply with it, and the W9 CLI service factory's ``teclaw-live``
        binding pushes through it on either mode, that path having no closing
        step of its own.

        The device graph is reached lazily and by function-level import for the
        reason the device bundle above records: it reaches the device dispatcher
        graph at import time.
        """
        def resolve(bot_id: str, owner_id: str):
            from agentclaw.community.core.devices.services.device_context_resolver import (
                DeviceContextResolver,
            )

            return injector.get(DeviceContextResolver).resolve_for_bot(bot_id, owner_id)

        def dispatch(device):
            from agentclaw.community.plugin_api.device_sync_dispatcher import (
                DeviceSyncDispatcher,
            )

            return injector.get(DeviceSyncDispatcher).dispatch(device)

        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
        )

        return TeclawRedeliver(
            resolve=resolve, dispatch=dispatch, not_bound=DeviceNotBoundError
        )

    # ── one provider per strategy ──────────────────────────────────────────

    @singleton
    @provider
    @inject
    def manifest_arca_delivery(self, device_ports: DevicePorts) -> ArcaDelivery:
        """The container family's strategy: ``script`` before the container,
        every other construct after it, through the device-backed bundle."""
        return ArcaDelivery(device_ports)

    @singleton
    @provider
    @inject
    def manifest_teclaw_platform_delivery(
        self, platform_ports: PlatformPorts, redeliver: Redeliver
    ) -> TeclawPlatformDelivery:
        """The artifact family delivering through platform state (W8, spec D-3).

        Both collaborators are bindings of their own, so this provider names
        what the shape needs and constructs nothing else. Its constructor
        *requires* the redeliver, which is what makes "platform-managed with
        nothing to be platform-managed with" unexpressible.
        """
        return TeclawPlatformDelivery(ports=platform_ports, redeliver=redeliver)

    @singleton
    @provider
    @inject
    def manifest_teclaw_device_delivery(
        self,
        device_ports: DevicePorts,
        cli_tool_service_factory: CliToolServiceFactory,
    ) -> TeclawDeviceDelivery:
        """The artifact family delivering the way it did before W8.

        ARCA's own bundle, with W9's teclaw-bound CLI service substituted in, so
        a teclaw bot never gets the ARCA delivery port for a category that is
        always platform-managed. The substitution is a thunk over the same
        factory ARCA's bundle takes its own binding from, not a second
        component: which binding a key names is that factory's answer, and this
        is the caller that needs the teclaw one.

        Its constructor does not take a redeliver and this provider does not
        ask for one, which is why a device-backed deployment cannot redeliver by
        accident.
        """
        return TeclawDeviceDelivery(
            ports=device_ports,
            cli_tool_service=lambda: cli_tool_service_factory("teclaw"),
        )

    # ── which of them each family delivers through ─────────────────────────

    @singleton
    @multiprovider
    @inject
    def manifest_arca_delivery_strategy(
        self, arca: ArcaDelivery
    ) -> DeliveryStrategies:
        """The ARCA row. No mode is consulted — the switch is a teclaw-only
        fact, and a deployment cannot run ARCA any other way."""
        return {EngineFamily.ARCA: arca}

    @singleton
    @multiprovider
    @inject
    def manifest_teclaw_platform_delivery_strategy(
        self,
        platform: TeclawPlatformDelivery,
        manifest_config: cfg.BotConfigManifestConfig,
    ) -> DeliveryStrategies:
        """The teclaw row on a ``PLATFORM`` deployment — and nothing on a
        ``DEVICE`` one.

        An empty mapping when the deployment is not this mode, which is how a
        provider says "not me" to a map multibinding: the strategy stays bound
        for anything that asks for it by name, and does not become the family's
        answer, so the sibling provider's row is the one an apply reaches.
        """
        if manifest_config.teclaw_delivery_mode is not TeclawDeliveryMode.PLATFORM:
            return {}
        return {EngineFamily.TECLAW: platform}

    @singleton
    @multiprovider
    @inject
    def manifest_teclaw_device_delivery_strategy(
        self,
        device: TeclawDeviceDelivery,
        manifest_config: cfg.BotConfigManifestConfig,
    ) -> DeliveryStrategies:
        """The teclaw row on a ``DEVICE`` deployment — and nothing on a
        ``PLATFORM`` one. The sibling above records how the empty row reads."""
        if manifest_config.teclaw_delivery_mode is not TeclawDeliveryMode.DEVICE:
            return {}
        return {EngineFamily.TECLAW: device}

    # ── the lookup over them ───────────────────────────────────────────────

    @singleton
    @provider
    @inject
    def manifest_engine_family_of(
        self, teclaw_engine_test_factory: Callable[[], TeclawEngineTestProtocol]
    ) -> EngineFamilyOf:
        """Which family a bot's engine type is.

        The engine authority answers a boolean because that is the question the
        rest of the platform asks it; the delivery seam keys by
        :class:`EngineFamily`, so it is adapted once, here, at the boundary.

        It wraps the same authority the capability resolver and the CLI-tool
        surface take, so the three cannot disagree about what a bot is. Lazy
        for the same reason they take it lazily.
        """
        return family_from_engine_test(
            lambda engine: teclaw_engine_test_factory().is_teclaw(engine)
        )

    @singleton
    @provider
    @inject
    def manifest_delivery_strategies(
        self,
        strategies: DeliveryStrategies,
        family_of: EngineFamilyOf,
    ) -> DeliveryStrategyFactory:
        """W8: the lookup the apply service asks per apply and per bot.

        Two arguments, both bound: the strategies the providers above
        contributed — one per family, with the teclaw row coming from whichever
        of the two mode providers was effective — and the family adapter. So
        this provider builds no strategy, reads no config and holds no mode;
        it hands the factory the mapping the graph assembled and lets its own
        constructor refuse a family that has none.
        """
        return DeliveryStrategyFactory(family_of=family_of, strategies=strategies)
