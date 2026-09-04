"""Provider-behavior seam — deploy-time behavior that varies by ``device_provider``.

The publish flow's stage logic is provider-agnostic: instead of scattered
``if resolve_container_provider(bot) == TECLAW`` / ``if active_engine == "teclaw"``
branches, each provider-varying step is a method on a :class:`ProviderBehavior`
selected by ``device_provider`` via :class:`ProviderBehaviorRouter` (mirroring
``DeployArtifactProducerRouter``).

This is deliberately distinct from the artifact *producer* seam
(``deploy/producer.py``): the producer *builds* the artifact for a
``(bot, version)`` at build time; this owns the *deploy-time* steps that differ
by container — post-build file staging, post-upgrade MCP-rule refresh, whether
scale is supported, whether an upgrade can revive a not-live bot, and whether the
verify bot is destroyed on online success.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.ext_state import (
    PublishExtState,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.bot_build_service import (
        BotBuildService,
    )
    from agentclaw.community.core.service_bot.services.deploy.teclaw_file_promotion import (
        TeclawFilePromotion,
    )
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.di.modules.skill_center_module import (
        DeviceFilesystemDispatcher,
    )

logger = get_logger()


class ProviderBehavior(ABC):
    """Deploy-time steps that vary by container provider.

    Concrete providers (:class:`DefaultProviderBehavior`, :class:`TeclawProviderBehavior`)
    inherit explicitly and implement every abstract member.
    """

    @abstractmethod
    async def stage_build_files(
        self, *, artifact: Any, bot: dict, bot_id: str, owner_id: str, publish_id: int
    ) -> None:
        """Snapshot provider-owned live files into the just-composed artifact
        (teclaw); a no-op for providers whose files already mirror to storage."""
        ...

    @abstractmethod
    def refresh_after_upgrade(self, *, bot_uuid: str, bot: dict) -> None:
        """Re-establish anything a rebuild-less upgrade does not (teclaw MCP
        outbound rule); a no-op for providers that refresh via a startup callback."""
        ...

    @abstractmethod
    def persist_stage_promotion(
        self, *, ext: dict, stage: PublishStage, engine_overrides: dict | None
    ) -> None:
        """Persist this provider's per-stage promotion state into the record's
        ``ext`` when a release for ``stage`` is recorded (teclaw stamps the frozen
        artifact snapshot + stores the stage's channel overrides so a restart
        reproduces them); a no-op for providers that carry no such snapshot."""
        ...

    @abstractmethod
    def validate_draft_restore_artifact(self, artifact_ext: dict) -> str | None:
        """Return ``None`` when the historical artifact can restore a draft,
        otherwise a user-facing reason explaining the missing provider artifact."""
        ...

    @abstractmethod
    async def restore_draft(
        self,
        *,
        build_service: "BotBuildService",
        bot: dict,
        bot_uuid: str,
        owner_id: str,
        source_version: int,
        artifact_ext: dict,
        baas_publish_id: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Advance one restore step for the provider's historical artifact."""
        ...

    @property
    @abstractmethod
    def draft_restore_uses_workflow(self) -> bool:
        """Whether restore is backed by a trackable BaaS workflow."""
        ...

    @property
    @abstractmethod
    def supports_scale(self) -> bool:
        """Whether this provider's service bots support scale."""
        ...

    @property
    @abstractmethod
    def upgrade_recovers_not_live_bot(self) -> bool:
        """Whether an upgrade can revive a candidate BaaS bot that is no longer
        live (``FAILED`` / ``STOPPED``). ``True`` when the provider's UPDATE
        destroys and recreates the device in place; ``False`` when it cannot
        rebuild a gone container, so the online deploy must retire the candidate
        and release a fresh one instead."""
        ...

    @property
    @abstractmethod
    def destroys_verify_bot_on_online(self) -> bool:
        """Whether the verify-stage BaaS bot is torn down after online success."""
        ...


class DefaultProviderBehavior(ProviderBehavior):
    """ARCA / baas container behavior — the historical default (no teclaw steps).

    Files already mirror to storage (no snapshot needed), an ARCA upgrade rebuilds
    the container and refreshes MCP auth via its startup callback (so an upgrade
    also revives a FAILED/STOPPED bot), scale is supported, and the verify bot is
    destroyed once online succeeds.
    """

    async def stage_build_files(
        self, *, artifact: Any, bot: dict, bot_id: str, owner_id: str, publish_id: int
    ) -> None:
        return None

    def refresh_after_upgrade(self, *, bot_uuid: str, bot: dict) -> None:
        return None

    def persist_stage_promotion(
        self, *, ext: dict, stage: PublishStage, engine_overrides: dict | None
    ) -> None:
        # ARCA/baas keep no frozen artifact snapshot and route per-stage channels
        # out of band → nothing to persist here.
        return None

    def validate_draft_restore_artifact(self, artifact_ext: dict) -> str | None:
        if artifact_ext.get("migration_path"):
            return None
        return "上一版本没有可用的 migration_path 构造物"

    async def restore_draft(
        self,
        *,
        build_service: "BotBuildService",
        bot: dict,
        bot_uuid: str,
        owner_id: str,
        source_version: int,
        artifact_ext: dict,
        baas_publish_id: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        del bot_uuid, owner_id, request_id
        if baas_publish_id is not None:
            raise PublishFlowServiceError("本地草稿恢复不应携带 BaaS publish_id")
        return await build_service.restore_draft_async(
            bot=bot,
            source_version=source_version,
            artifact_ext=artifact_ext,
        )

    @property
    def draft_restore_uses_workflow(self) -> bool:
        return False

    @property
    def supports_scale(self) -> bool:
        return True

    @property
    def upgrade_recovers_not_live_bot(self) -> bool:
        # The ARCA/baas UPDATE destroys + recreates the device in place, so a
        # FAILED/STOPPED candidate is recovered by upgrading it.
        return True

    @property
    def destroys_verify_bot_on_online(self) -> bool:
        return True


class TeclawProviderBehavior(ProviderBehavior):
    """Pull-based teclaw container behavior.

    The running source container owns its live files (the backend keeps no mirror
    of them), so a build snapshots ``/workspace`` + identity files into OSS and
    embeds the refs;
    a teclaw upgrade has no startup callback, so the MCP outbound rule is refreshed
    explicitly; its UPDATE cannot rebuild a gone container, so a not-live bot is
    retired rather than upgraded; teclaw service bots do not support scale; and the
    verify bot is kept (not destroyed) when online succeeds.
    """

    def __init__(
        self,
        *,
        build_service: "BotBuildService",
        resolver: "DeviceContextResolver",
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
        teclaw_file_promotion: "TeclawFilePromotion",
    ) -> None:
        self._build_service = build_service
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher
        self._teclaw_file_promotion = teclaw_file_promotion

    async def stage_build_files(
        self, *, artifact: Any, bot: dict, bot_id: str, owner_id: str, publish_id: int
    ) -> None:
        config_artifact = artifact.ext.get("config_artifact")
        if not isinstance(config_artifact, dict):
            raise PublishFlowServiceError(
                f"teclaw build produced no config_artifact dict for bot={bot_id}"
            )
        ctx_dev = self._resolver.resolve_for_bot(bot_id, owner_id)
        device_fs = self._device_fs_dispatcher.dispatch(ctx_dev)
        refs = await self._teclaw_file_promotion.stage_files(
            device_fs=device_fs,
            env=get_current_env(),
            entity_type=bot.get("entity_type", "staff"),
            entity_id=bot.get("entity_id", ""),
            bot_id=bot_id,
            publish_id=publish_id,
            # Option 1: the artifact is composed once at build and reused by verify
            # + online, so the stage segment is fixed to the build target (verify).
            # It only namespaces the OSS snapshot; the snapshot is not re-taken.
            stage=PublishStage.VERIFY.value,
        )
        config_artifact.setdefault("resources", []).extend(refs.resources)
        config_artifact.setdefault("identity_files", []).extend(refs.identity_files)
        if refs.cli_tools:
            # **Replaced**, not extended, and that is the difference between a
            # gather and a copy. The composer already put a ref per tool in this
            # artifact, pointing at the platform's live prefix; promotion copies
            # the same objects into the stage's prefix and these refs point at
            # the copies. Extending would leave two refs per command.
            #
            # An empty list leaves the composer's answer alone rather than
            # writing ``cli_tools: []``: a bot with no tools must compose an
            # artifact with the key absent, byte-identical to a pre-W9 one.
            config_artifact["cli_tools"] = list(refs.cli_tools)
        logger.info(
            "[TeclawProviderBehavior.stage_build_files] merged %d resource(s) + "
            "%d identity file(s) + %d cli tool(s) into artifact for bot=%s "
            "publish_id=%s",
            len(refs.resources), len(refs.identity_files), len(refs.cli_tools),
            bot_id, publish_id,
        )

    def refresh_after_upgrade(self, *, bot_uuid: str, bot: dict) -> None:
        self._build_service.refresh_teclaw_mcp_outbound_rule(bot_uuid=bot_uuid, bot=bot)

    def persist_stage_promotion(
        self, *, ext: dict, stage: PublishStage, engine_overrides: dict | None
    ) -> None:
        # Stamp the promoted stage into the stored config_artifact snapshot and
        # store this stage's channel overrides next to the binding/publish refs, so
        # a restart/redeliver reproduces the promoted channels. Both no-op when the
        # respective key is absent.
        PublishExtState.stamp_stage_on_stored_artifact(ext, stage)
        PublishExtState.store_stage_overrides(ext, stage, engine_overrides)

    def validate_draft_restore_artifact(self, artifact_ext: dict) -> str | None:
        config_artifact = artifact_ext.get("config_artifact")
        if not isinstance(config_artifact, dict):
            return "上一版本没有可用的 config_artifact 构造物"
        if config_artifact.get("engine_type") != TECLAW_DEVICE_PROVIDER:
            return "上一版本的 config_artifact 不是 teclaw 构造物"
        return None

    async def restore_draft(
        self,
        *,
        build_service: "BotBuildService",
        bot: dict,
        bot_uuid: str,
        owner_id: str,
        source_version: int,
        artifact_ext: dict,
        baas_publish_id: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        return await build_service.restore_teclaw_draft_async(
            bot_uuid=bot_uuid,
            bot=bot,
            owner_id=owner_id,
            source_version=source_version,
            artifact_ext=artifact_ext,
            baas_publish_id=baas_publish_id,
            request_id=request_id,
        )

    @property
    def draft_restore_uses_workflow(self) -> bool:
        return True

    @property
    def supports_scale(self) -> bool:
        return False

    @property
    def upgrade_recovers_not_live_bot(self) -> bool:
        # A teclaw UPDATE cannot rebuild a gone container — it would just fail the
        # publish and strand the record, so a not-live candidate is retired and
        # replaced by a fresh first release.
        return False

    @property
    def destroys_verify_bot_on_online(self) -> bool:
        return False


class ProviderBehaviorRouter:
    """Select a :class:`ProviderBehavior` by ``device_provider``.

    Pure dispatch over a DI-assembled map with a default fallback — same shape as
    ``DeployArtifactProducerRouter``.
    """

    def __init__(
        self,
        behaviors: dict[str, ProviderBehavior],
        default_provider_key: str,
    ) -> None:
        if default_provider_key not in behaviors:
            raise ValueError(
                f"default_provider_key {default_provider_key!r} not in behaviors "
                f"{list(behaviors.keys())!r}"
            )
        self._behaviors: dict[str, ProviderBehavior] = dict(behaviors)
        self._default_key = default_provider_key

    def resolve(self, device_provider: str | None) -> ProviderBehavior:
        if device_provider and device_provider in self._behaviors:
            return self._behaviors[device_provider]
        return self._behaviors[self._default_key]
