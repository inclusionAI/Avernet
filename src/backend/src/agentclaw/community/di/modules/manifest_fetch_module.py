"""ManifestFetchModule — the fetch-side machine parts of manifest apply (W5).

The apply wiring W11's repository binding comment reserved: the guarded
fetcher (W2), the content store (W11), and the one entry-fetcher funnel over
W2/W3/W11, plus the lazy service factories the registry's fetch-consuming
materialisers take their collaborators through. One module because one
feature wave owns them — the same reason the apply service and its
repositories bind in ``bot_management_module``, where they landed with W4.

The config cluster lives here rather than in ``config_module`` because that
file sits at the 1000-line cap; the sofa read itself still happens there,
through the one public seam (``read_user_config``), while the parsing stays
with W2's and W11's own pure parsers — the yaml keys and their consumers
remain defined together.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from injector import Injector, Module, inject, provider, singleton

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    CliToolPurger,
    CliToolService,
    CliToolServiceFactory,
)
from agentclaw.community.api.bot_cli_tool_service import BotCliToolServiceProtocol
from agentclaw.community.core.bot_config_manifest.apply.identity_port import (
    ManifestIdentityPort,
)
from agentclaw.community.core.bot_config_manifest.apply.resource_port import (
    ManifestResourcePort,
)
from agentclaw.community.core.bot_config_manifest.content.service import (
    ManifestContentService,
)
from agentclaw.community.core.bot_config_manifest.content.service_protocol import (
    ManifestContentServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceClient,
    SubprocessGitClient,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    GuardedFetcher,
)
from agentclaw.community.core.repository.protocols.bot.cli_tool import (
    BotCliToolRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    ManifestContentRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageValidator,
)
from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.core.bot_config_manifest.managed_files import (
    ManagedFilesComposeReader,
    ManagedFilesStore,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    MaterialiserPorts,
    TeclawPlatformBindings,
)
from agentclaw.community.core.bot_config_manifest.apply.record_only_activation import (
    RecordOnlyActivation,
)
from agentclaw.community.core.bot_config_manifest.apply.redeliver import TeclawRedeliver
from agentclaw.community.core.bot_config_manifest.managed_files.ports import (
    StoreIdentityPort,
    StoreResourcePort,
    StoreSkillPackagePort,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_auth_service_protocol import MCPAuthServiceProtocol
from agentclaw.community.core.bot_startup_script.protocols import (
    TeclawEngineTestProtocol,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.di.modules.config_module import read_user_config
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


class ManifestFetchModule(Module):
    """Wire the fetch pipeline the ``skills`` and ``identity`` materialisers run on."""

    # ── the config cluster ─────────────────────────────────────────────────

    @singleton
    @provider
    def bot_config_manifest(self) -> cfg.BotConfigManifestConfig:
        """Manifest apply composition (the ``user_config.bot_config_manifest``
        block; neutral defaults, env overlays decide a mirror or a NAS volume).

        Parsed by each machine part's own seam — transport allowlist by W2's
        ``transport_allowlist_from_config``, blob root by W11's
        ``content_store_root_from_config`` — so the yaml keys and their
        consumers stay defined together. This provider is only the one sofa
        read, through config_module's public seam.
        """
        from agentclaw.community.core.bot_config_manifest.apply.delivery import (
            teclaw_platform_managed_from_config,
        )
        from agentclaw.community.core.bot_config_manifest.content.settings import (
            content_store_root_from_config,
        )
        from agentclaw.community.core.bot_config_manifest.fetch.limits import (
            transport_allowlist_from_config,
        )

        tree = read_user_config()
        return cfg.BotConfigManifestConfig(
            fetch_transport_allowlist=tuple(
                sorted(transport_allowlist_from_config(tree))
            ),
            content_store_dir=str(content_store_root_from_config(tree)),
            # W8's switch, parsed by the delivery seam's own reader so the yaml
            # key and its consumer stay defined together.
            teclaw_platform_managed=teclaw_platform_managed_from_config(tree),
        )

    # ── the machine parts ──────────────────────────────────────────────────

    @singleton
    @provider
    @inject
    def manifest_managed_files_store(
        self, object_storage: ObjectStoragePlugin
    ) -> ManagedFilesStore:
        """W8: the platform's own copy of a teclaw bot's manifest-delivered files.

        Bytes in the same OSS bucket the teclaw promotion stages into, under the
        same ``bot-data`` base, so the composer's store coordinates resolve a
        manifest-delivered ref exactly as they resolve a promoted one. The key
        layout is the record; there is no index table beside it.
        """
        from agentclaw.community.core.storage.path import get_teclaw_bolt_data_prefix

        return ManagedFilesStore(
            object_storage=object_storage, store_base=get_teclaw_bolt_data_prefix
        )

    @singleton
    @provider
    @inject
    def manifest_managed_files_reader(
        self,
        store: ManagedFilesStore,
        injector: Injector,
        manifest_config: cfg.BotConfigManifestConfig,
    ) -> ManagedFilesComposeReader:
        """W8: what the teclaw composer reads — which categories the platform
        asserts, and the refs it holds for them. The manifest service is lazy
        for the cycle reason every other manifest collaborator is."""
        return ManagedFilesComposeReader(
            store=store,
            manifest_service_provider=lambda: injector.get(
                BotConfigManifestServiceProtocol
            ),
            platform_managed=lambda: manifest_config.teclaw_platform_managed,
        )

    @singleton
    @provider
    def manifest_guarded_fetcher(
        self, manifest_config: cfg.BotConfigManifestConfig
    ) -> GuardedFetcher:
        """The W2 transport, constructed from the typed config cluster.

        The allowlist is a deployment decision (an internal mirror or proxy);
        everything else about the fetcher — scheme, address pinning, redirect
        budget — is the shipped default and not configurable from here.
        """
        return GuardedFetcher(
            transport_allowlist=manifest_config.fetch_transport_allowlist
        )

    @singleton
    @provider
    @inject
    def manifest_content_service(
        self,
        repository: ManifestContentRepositoryProtocol,
        manifest_config: cfg.BotConfigManifestConfig,
    ) -> ManifestContentServiceProtocol:
        """The W11 content store: blob root from the typed config cluster."""
        return ManifestContentService(
            repository,
            Path(manifest_config.content_store_dir),
        )

    @singleton
    @provider
    @inject
    def manifest_entry_fetcher(
        self,
        fetcher: GuardedFetcher,
        content: ManifestContentServiceProtocol,
        credentials: SourceCredentialServiceProtocol,
    ) -> EntryFetcher:
        """The one fetch funnel the fetch-consuming materialisers share.

        One instance over the three singletons: the transport, the store, and
        W3's credentials — so every category that fetches reads the same
        receipts and files the same provenance rows.
        """
        return EntryFetcher(fetcher, content, credentials)

    @singleton
    @provider
    def manifest_git_source_client(self) -> GitSourceClient:
        """W7's git transport: the CLI subprocess client.

        Like ``GuardedFetcher``, everything about it except construction is
        the shipped default — https-only scheme, hermetic env, header-only
        credential injection — and not configurable from here.

        The base subprocess environment is read **here**, the composition
        root, per the repo rule that raw environment access belongs to
        configuration loading and composition roots, never to core. The
        client drops every ``GIT_*`` key this snapshot still carries and adds
        its own hermetic overrides, so this env is a plain inheritance
        surface: proxy settings ride along exactly as they do for the W2
        httpx transport (``trust_env`` is that client's default), while an
        operator's ``insteadOf`` rewrite or upload-pack default cannot.
        """
        import os

        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        return SubprocessGitClient(env=env)

    # ── the lazy factories the registry wiring asks for ────────────────────

    @singleton
    @provider
    @inject
    def manifest_identity_service_factory(
        self, injector: Injector
    ) -> Callable[[], ManifestIdentityPort]:
        """The identity service the ``identity`` materialiser writes through.

        Lazy with a function-level import for the reason
        ``service_bot_module`` records at its own use: the identity module
        reaches the device dispatcher graph at import time, and this
        module's import must not trigger that chain.
        """
        from agentclaw.community.core.bot_config_manifest.apply.identity_port import (
            ManifestIdentityPort,
        )
        from agentclaw.community.core.services.identity import IdentityService

        def _identity() -> ManifestIdentityPort:
            service = injector.get(IdentityService)
            if not isinstance(service, ManifestIdentityPort):
                # Structural check at wiring time: the port has no
                # implementation relationship to the service, so nothing
                # else would notice a renamed method until mid-apply. The
                # drift guard belongs where the two first meet.
                raise TypeError(
                    "IdentityService no longer satisfies ManifestIdentityPort"
                )
            return service

        return _identity

    @singleton
    @provider
    @inject
    def manifest_skill_upload_service_factory(
        self, injector: Injector
    ) -> Callable[[], LocalSkillUploadServiceProtocol]:
        """The upload service the ``skills`` materialiser installs through.

        The manual-upload road: the same ``upload_local_skill`` entry point
        the raw-zip router path takes, so a manifest-installed skill and an
        uploaded one are one thing, not two alike ones.
        """
        return lambda: injector.get(LocalSkillUploadServiceProtocol)

    @singleton
    @provider
    @inject
    def manifest_capability_reader_factory(
        self, injector: Injector
    ) -> Callable[[], BotCapabilityStateReaderProtocol]:
        """The flush-then-read capability state the ``skills`` materialiser
        enumerates its area from (and narrows removals by)."""
        return lambda: injector.get(BotCapabilityStateReaderProtocol)

    @singleton
    @provider
    @inject
    def manifest_package_validator_factory(
        self, injector: Injector
    ) -> Callable[[], SkillPackageValidator]:
        """The package gate the ``skills`` materialiser validates fetched
        bytes with — the upload path's own validator, over the real parser.

        Built per call: the validator is stateless, and a fresh build here
        carries no singleton lifetime of its own to reason about.
        """
        from agentclaw.community.core.skill_center.services.skill_parser import (
            SkillParser,
        )

        return lambda: SkillPackageValidator(SkillParser())

    @singleton
    @provider
    @inject
    def manifest_entry_fetcher_factory(
        self, injector: Injector
    ) -> Callable[[], EntryFetcher]:
        """The lazy lookup the apply service's registry wiring asks for."""
        return lambda: injector.get(EntryFetcher)

    @singleton
    @provider
    @inject
    def manifest_resource_service_factory(
        self, injector: Injector
    ) -> Callable[[], ManifestResourcePort]:
        """The write chain the ``resources`` materialiser delivers through.

        Lazy with a function-level import for the reason the identity
        factory above records: the resource file service module reaches the
        device dispatcher graph at import time, and this module's import
        must not trigger that chain. Structural check at wiring time for
        the same reason as there — the port has no implementation
        relationship to the service, so nothing else would notice a rename
        until mid-apply.
        """
        from agentclaw.community.core.services.resource_file_service import (
            ResourceFileService,
        )

        def _resources() -> ManifestResourcePort:
            service = injector.get(ResourceFileService)
            if not isinstance(service, ManifestResourcePort):
                raise TypeError(
                    "ResourceFileService no longer satisfies ManifestResourcePort"
                )
            return service

        return _resources

    @singleton
    @provider
    @inject
    def manifest_cli_tool_service_factory(
        self,
        injector: Injector,
        object_storage: ObjectStoragePlugin,
        entry_fetcher_provider: Callable[[], EntryFetcher],
    ) -> CliToolServiceFactory:
        """W9: the one component both callers install a CLI tool through.

        Parameterised by engine family and by nothing else, because that is the
        only thing that differs: the table, the object store and the fetch
        funnel are shared, and the family decides which delivery port sits
        inside. The apply service asks for ``"arca"``, the teclaw platform
        ports for ``"teclaw"``, and the management API for whichever the bot's
        engine resolves to — all three getting the same service around the same
        table, which is what makes the API and a manifest apply converge on one
        answer.

        The device graph is reached lazily and by function-level import for the
        reason the resource factory above records: it reaches the device
        dispatcher graph at import time.
        """
        from agentclaw.community.core.bot_config_manifest.cli_tools.store import (
            CliToolStore,
        )
        from agentclaw.community.core.bot_config_manifest.cli_tools.teclaw_port import (
            TeclawCliToolPort,
        )
        from agentclaw.community.core.repository.protocols.bot.cli_tool import (
            BotCliToolRepositoryProtocol,
        )
        from agentclaw.community.core.storage.path import get_teclaw_bolt_data_prefix

        store = CliToolStore(
            object_storage=object_storage, store_base=get_teclaw_bolt_data_prefix
        )

        def _delivery(family: str):
            if family == "teclaw":
                return TeclawCliToolPort()
            from agentclaw.community.core.bot_config_manifest.cli_tools.arca_port import (
                ArcaCliToolPort,
            )
            from agentclaw.community.core.devices.services.device_context_resolver import (
                DeviceContextResolver,
            )
            from agentclaw.community.plugin_api.device_adapter_transport import (
                DeviceAdapterTransport,
            )

            return ArcaCliToolPort(
                resolver=injector.get(DeviceContextResolver),
                transport=injector.get(DeviceAdapterTransport),
            )

        def _factory(family: str) -> CliToolService:
            return CliToolService(
                repo=injector.get(BotCliToolRepositoryProtocol),
                store=store,
                delivery=_delivery(family),
                entry_fetcher=entry_fetcher_provider(),
            )

        return _factory

    @singleton
    @provider
    @inject
    def cli_tool_purger(
        self, object_storage: ObjectStoragePlugin, injector: Injector
    ) -> CliToolPurger:
        """W9: what a failed creation's discard calls.

        Not the service: a creation that ended without a bot has no container,
        so this drops rows and objects and reaches no engine at all.
        """
        from agentclaw.community.core.bot_config_manifest.cli_tools.store import (
            CliToolStore,
        )
        from agentclaw.community.core.storage.path import get_teclaw_bolt_data_prefix

        return CliToolPurger(
            repo=injector.get(BotCliToolRepositoryProtocol),
            store=CliToolStore(
                object_storage=object_storage, store_base=get_teclaw_bolt_data_prefix
            ),
        )

    @singleton
    @provider
    @inject
    def bot_cli_tool_repository(
        self, injector: Injector
    ) -> BotCliToolRepositoryProtocol:
        """W9: ``ac_bot_cli_tool``, the platform's record of a bot's tools.

        Bound here rather than beside the other bot repositories because the
        module that holds them is at its size cap; the table belongs to this
        feature and every consumer of it is wired in this module.
        """
        from agentclaw.community.core.repository.implementations.bot.cli_tool import (
            BotCliToolRepository,
        )

        return injector.get(BotCliToolRepository)

    @singleton
    @provider
    @inject
    def bot_cli_tool_service(
        self,
        injector: Injector,
        cli_tool_service_factory: CliToolServiceFactory,
        teclaw_engine_test_factory: Callable[[], TeclawEngineTestProtocol],
    ) -> BotCliToolServiceProtocol:
        """W9: the ``bot_id``-addressed surface the HTTP routes bind to.

        A thin resolver over the same factory the apply service uses, so the
        management API and a manifest apply reach one implementation of every
        step. The engine test is the same factory the apply service takes, so
        the two arms cannot disagree about which family a bot is; the bot
        service is looked up lazily for the reason every provider in this
        module is lazy — it reaches the graph that builds this one.
        """
        from agentclaw.community.core.bot_config_manifest.cli_tools.bot_service import (
            BotCliToolService,
        )

        class _Bots:
            """Defers the bot-service lookup to the call, not to DI time."""

            def get_bot(self, bot_id: str, owner_id: str) -> dict:
                from agentclaw.community.api.bot_service import BotServiceProtocol

                return injector.get(BotServiceProtocol).get_bot(bot_id, owner_id)

        return BotCliToolService(
            bot_service=_Bots(),
            cli_tool_service_factory=cli_tool_service_factory,
            is_teclaw=lambda engine: teclaw_engine_test_factory().is_teclaw(engine),
        )

    @singleton
    @provider
    @inject
    def manifest_git_client_factory(
        self, injector: Injector
    ) -> Callable[[], GitSourceClient]:
        """The lazy lookup the apply service builds each apply's source
        session through — a fresh map and fresh checkouts every apply."""
        return lambda: injector.get(GitSourceClient)

    # ── W8: the platform-managed teclaw path ───────────────────────────────

    @singleton
    @provider
    def bot_create_with_manifest_config(self) -> cfg.BotCreateWithManifestConfig:
        """W13's creation policy, read through ``config_module``'s public seam.

        Parsed here, with the manifest config cluster: the read goes through
        the one seam, and the parsing lives with the graph that consumes it.
        """
        return cfg.BotCreateWithManifestConfig.from_block(
            read_user_config().get("bot_create_with_manifest") or {}
        )

    @singleton
    @provider
    @inject
    def manifest_teclaw_platform_bindings(
        self,
        injector: Injector,
        store: ManagedFilesStore,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        activation_service_provider: Callable[[], DirectActivationServiceProtocol],
        mcp_auth_service_provider: Callable[[], MCPAuthServiceProtocol],
        capability_reader_provider: Callable[[], BotCapabilityStateReaderProtocol],
        package_validator_provider: Callable[[], SkillPackageValidator],
        entry_fetcher_provider: Callable[[], EntryFetcher],
        cli_tool_service_factory: CliToolServiceFactory,
    ) -> TeclawPlatformBindings:
        """The store-backed ports and the closing redeliver (W8, spec D-7).

        The three file categories write to the managed-files store instead of
        a container; activation records without projecting; the closing step
        hands the running container the whole artifact once. Everything the
        family shares with ARCA — the script service, the permission check,
        the capability reader, the validator, the fetch pipeline — is the
        same object ARCA's ports carry.

        The device graph is resolved lazily and by function-level import for
        the reason the resource factory above records: it reaches the device
        dispatcher graph at import time.
        """
        def platform_ports() -> MaterialiserPorts:
            validator = package_validator_provider()
            return MaterialiserPorts(
                script_service=script_service_provider(),
                activation_service=RecordOnlyActivation(activation_service_provider()),
                mcp_auth_service=mcp_auth_service_provider(),
                identity_service=StoreIdentityPort(store),
                upload_service=StoreSkillPackagePort(
                    store,
                    validator=validator,
                    skill_repository=injector.get(SkillRepository),
                ),
                capability_reader=capability_reader_provider(),
                package_validator=validator,
                entry_fetcher=entry_fetcher_provider(),
                resource_service=StoreResourcePort(store),
                # W9 is always platform-managed and never consults the switch,
                # as ``mcp`` does not: the artifact is the delivery on this
                # family whatever the switch says.
                cli_tool_service=cli_tool_service_factory("teclaw"),
            )

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

        return TeclawPlatformBindings(
            platform_ports=platform_ports,
            redeliver=TeclawRedeliver(
                resolve=resolve, dispatch=dispatch, not_bound=DeviceNotBoundError
            ),
        )
