"""Singlebox-only producer router override.

Singlebox runs on a developer laptop (or CI) with **no ARCA/ECS sandbox**, so
the engine's bot files live on the **localhost filesystem** under the
configured engine root (``openclaw_root`` → ``~/.openclaw``), never under the
production per-bot NAS merge area. The base
``ServiceBotModule`` wires the publish build's ``DeployArtifactProducerRouter``
to point ``arca``/``baas`` at :class:`ArcaSnapshotProducer`, which computes
the build source via ``get_bot_nas_dir(...)`` and runs ``sudo chmod`` +
``sudo rsync`` against it — both abort on a dev laptop (``sudo: a password is
required``; the NAS path does not exist), failing the publish build stage.

This module re-binds ``DeployArtifactProducerRouter`` so singlebox routes the
publish build through :class:`LocalBuildProducer`, which sources the artifact
from the engine's local working directory (``EngineSandboxProvider.get_base_path()``).
Following the existing singlebox override pattern (see ``SingleboxAccessModule``,
``SingleboxDevicesModule``), installing this module after the base
``ServiceBotModule`` makes the later binding win.

Why re-point ``"arca"`` and ``"baas"`` too (not just set ``default_provider_key``):
``DeployArtifactProducerRouter.resolve()`` returns the *hit* provider and only
falls back to the default when the key is absent. Non-teclaw bots resolve
their ``device_provider`` to ``"baas"`` (see ``provider_resolver.py``), which
*hits* the map — so merely changing the default would leave ``"baas"`` pointed
at the NAS producer and the local path would never be selected. All local
builds must land on :class:`LocalBuildProducer`, so the local keys
(``local``/``arca``/``baas``) are re-pointed here.

``teclaw`` keeps :class:`ExternalComposeProducer` — singlebox has teclaw
external-container bots too, and their compose-from-DB+OSS path is
NAS-independent by design.
"""

from __future__ import annotations

from injector import Injector, Module, inject, provider, singleton

from agentclaw.community.core.bot_management.engines.registry import resolve_bot_engine  # noqa: F401  # (kept for parity; module-level import surface)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.api.bot_capability_state_reader import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
)
from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
    ArcaSnapshotProducer,
)
from agentclaw.community.core.service_bot.services.deploy.external_compose_producer import (
    ExternalComposeProducer,
)
from agentclaw.community.core.service_bot.services.deploy.local_build_producer import (
    LocalBuildProducer,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifactProducerRouter,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    ServiceSkillsManifestBuilder,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreConfig,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.storage.path import get_skills_repo_path
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.log import get_logger

logger = get_logger()


class SingleboxServiceBotModule(Module):
    """Singlebox: route the publish build producer to the local source snapshot."""

    @singleton
    @provider
    @inject
    def local_build_producer(
        self,
        bot_build_service: BotBuildService,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        capability_reader: BotCapabilityStateReaderProtocol,
        center_store: CanonicalCenterStoreConfig,
        canonical_center_versions: CanonicalCenterVersionStore,
        sandbox_registry: EngineSandboxRegistry,
    ) -> LocalBuildProducer:
        """Local-source build snapshot producer for singlebox.

        Same ``ServiceSkillsManifestBuilder`` wiring as
        :meth:`ServiceBotModule.arca_snapshot_producer` — the Skills slice the
        build pins is engine-determined, not NAS/local-determined.
        """
        return LocalBuildProducer(
            build_service=bot_build_service,
            skills_manifest_builder=ServiceSkillsManifestBuilder(
                layout_repository,
                capability_reader,
                center_store.base_prefix,
                canonical_center_versions,
                get_skills_repo_path(),
            ),
            sandbox_registry=sandbox_registry,
        )

    @singleton
    @provider
    @inject
    def deploy_artifact_producer_router(
        self,
        local_producer: LocalBuildProducer,
        external_producer: ExternalComposeProducer,
    ) -> DeployArtifactProducerRouter:
        """Override base ``ServiceBotModule`` so singlebox builds from the local
        engine source. Re-points ``local``/``arca``/``baas`` to
        :class:`LocalBuildProducer`` (see module docstring on why re-pointing
        both keys is required); ``teclaw`` keeps the compose-from-DB producer.
        """
        logger.info(
            "[SINGLEBOX] DeployArtifactProducerRouter → LocalBuildProducer "
            "(local/arca/baas; teclaw unchanged)"
        )
        return DeployArtifactProducerRouter(
            providers={
                "local": local_producer,
                "arca": local_producer,
                "baas": local_producer,
                "teclaw": external_producer,
            },
            default_provider_key="local",
        )
