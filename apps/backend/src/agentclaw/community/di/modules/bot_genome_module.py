"""Composition of the Bot Genome Registry (RSI-03): repositories, the two services, the delivery seam.

Everything the registry reads a bot through is resolved lazily, through the
thunks the Manifest modules already bind (``Callable[[], X]``): the state
reader reaches the device dispatcher graph, and resolving it at boot would
close the same import cycle ``manifest_delivery_module`` documents. The
registry is handed a thunk and builds the reader on first use.

The seam: ``revision_applier`` binds :class:`PendingDeliveryApplier`, which
refuses every promotion before any ref moves. RSI-04 rebinds this one
provider to its direct-delivery applier; nothing else changes.
"""

from __future__ import annotations

from typing import Callable

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.bot_genome_promotion_service import GenomePromotionServiceProtocol
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.core.content_store import ContentStoreProtocol
from agentclaw.community.core.bot_config_manifest.delivery_mode import TeclawDeliveryMode
from agentclaw.community.core.bot_genome.capture import BotStateReader
from agentclaw.community.core.bot_genome.capture.bot_state import PlatformBotStateReader
from agentclaw.community.core.bot_genome.promotion import (
    GenomePromotionService,
    PendingDeliveryApplier,
    RevisionApplier,
)
from agentclaw.community.core.bot_genome.registry import GenomeRegistryService
from agentclaw.community.core.bot_genome.spec_checks import SpecChecks
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_startup_script.protocols import TeclawEngineTestProtocol
from agentclaw.community.core.mcp.mcp_auth_service_protocol import MCPAuthServiceProtocol
from agentclaw.community.core.mcp.mcp_config_service_protocol import MCPConfigServiceProtocol
from agentclaw.community.core.ports.identity_file_port import IdentityFilePort
from agentclaw.community.core.repository.implementations.bot.genome import (
    GenomePatchRepository,
    GenomePromotionRepository,
    GenomeRefRepository,
    GenomeRevisionRepository,
)
from agentclaw.community.core.repository.protocols.bot.genome import (
    GenomePatchRepositoryProtocol,
    GenomePromotionRepositoryProtocol,
    GenomeRefRepositoryProtocol,
    GenomeRevisionRepositoryProtocol,
)
from agentclaw.community.core.resources.resource_service_protocol import ResourceServiceFactoryProtocol
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skill_center.skill_service_factory_protocol import (
    SkillServiceFactoryProtocol,
)
from agentclaw.community.di import config as cfg


class BotGenomeModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(GenomeRevisionRepositoryProtocol, to=GenomeRevisionRepository, scope=singleton)
        binder.bind(GenomeRefRepositoryProtocol, to=GenomeRefRepository, scope=singleton)
        binder.bind(GenomePatchRepositoryProtocol, to=GenomePatchRepository, scope=singleton)
        binder.bind(GenomePromotionRepositoryProtocol, to=GenomePromotionRepository, scope=singleton)

    @singleton
    @provider
    @inject
    def genome_state_reader_factory(
        self,
        injector: Injector,
        identity: Callable[[], IdentityFilePort],
        capabilities: Callable[[], BotCapabilityStateReaderProtocol],
        activation: Callable[[], DirectActivationServiceProtocol],
        mcp_config: Callable[[], MCPConfigServiceProtocol],
        scripts: Callable[[], BotStartupScriptServiceProtocol],
        engine_test: Callable[[], TeclawEngineTestProtocol],
        manifest_config: cfg.BotConfigManifestConfig,
    ) -> Callable[[], BotStateReader]:
        """The device-delivered bot's state reader, built on first use (the graph reaches the device dispatcher)."""

        def build() -> BotStateReader:
            from agentclaw.community.core.services.resource_file_service import ResourceFileService

            return PlatformBotStateReader(
                identity=identity(),
                capabilities=capabilities(),
                skill_factory=injector.get(SkillServiceFactoryProtocol),
                resource_services=injector.get(ResourceServiceFactoryProtocol),
                resource_files=injector.get(ResourceFileService),
                activation=activation(),
                mcp_config=mcp_config(),
                scripts=scripts(),
                is_teclaw=engine_test().is_teclaw,
                teclaw_platform_managed=manifest_config.teclaw_delivery_mode is TeclawDeliveryMode.PLATFORM,
            )

        return build

    @singleton
    @provider
    @inject
    def genome_registry(
        self,
        revisions: GenomeRevisionRepositoryProtocol,
        refs: GenomeRefRepositoryProtocol,
        patches: GenomePatchRepositoryProtocol,
        promotions: GenomePromotionRepositoryProtocol,
        content: ContentStoreProtocol,
        state_reader: Callable[[], BotStateReader],
        validator: Callable[[], SkillPackageValidator],
        mcp_auth: Callable[[], MCPAuthServiceProtocol],
        mcp_config: Callable[[], MCPConfigServiceProtocol],
        applies: BotConfigManifestApplyServiceProtocol,
    ) -> GenomeRegistryProtocol:
        package_validator = validator()
        return GenomeRegistryService(
            revisions=revisions,
            refs=refs,
            patches=patches,
            promotions=promotions,
            content=content,
            state_reader=state_reader,
            checks=SpecChecks(validator=package_validator, mcp_auth=_Lazy(mcp_auth), mcp_config=_Lazy(mcp_config)),
            validator=package_validator,
            applies=applies,
        )

    @singleton
    @provider
    def revision_applier(self) -> RevisionApplier:
        """The delivery seam. RSI-04 rebinds this provider; nothing else on the promotion path changes."""
        return PendingDeliveryApplier()

    @singleton
    @provider
    @inject
    def genome_promotion(
        self,
        registry: GenomeRegistryProtocol,
        revisions: GenomeRevisionRepositoryProtocol,
        refs: GenomeRefRepositoryProtocol,
        promotions: GenomePromotionRepositoryProtocol,
        applier: RevisionApplier,
    ) -> GenomePromotionServiceProtocol:
        return GenomePromotionService(
            registry=registry, revisions=revisions, refs=refs, promotions=promotions, applier=applier
        )


class _Lazy:
    """A service resolved on first call: the MCP services sit behind thunks for the same cycle reason as above."""

    def __init__(self, thunk: Callable[[], object]) -> None:
        self._thunk = thunk
        self._service: object | None = None

    def __getattr__(self, name: str):
        if self._service is None:
            self._service = self._thunk()
        return getattr(self._service, name)
