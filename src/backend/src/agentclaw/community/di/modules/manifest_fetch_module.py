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
from agentclaw.community.di.modules.config_module import read_user_config


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
        )

    # ── the machine parts ──────────────────────────────────────────────────

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
        """
        return SubprocessGitClient()

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
    def manifest_git_client_factory(
        self, injector: Injector
    ) -> Callable[[], GitSourceClient]:
        """The lazy lookup the apply service builds each apply's source
        session through — a fresh map and fresh checkouts every apply."""
        return lambda: injector.get(GitSourceClient)
