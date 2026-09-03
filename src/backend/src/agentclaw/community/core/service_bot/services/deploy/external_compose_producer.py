"""``ExternalComposeProducer`` — external/pull-based build producer (base).

Produces a deployable artifact by **composing from DB+NAS** (instead of ARCA's
snapshot-from-container), then materializing a content snapshot and freezing the
version. Concrete engines subclass this and override :meth:`_fetch_engine_ext`
to fetch their opaque ``engine_ext`` at build time (e.g. ``TeclawComposeProducer``
talks to teclaw servers).

Ownership note: the **producer** owns ``engine_ext`` fetching (build-time, frozen
into the version), not the composer — so the composer stays engine-agnostic.

The composed artifact is **refs-only** (every file is a ``{store, path}`` pointer;
bytes are persisted separately), so there's nothing to materialize —
``produce_artifact`` pins the composed dict straight onto ``ext`` as
``config_artifact`` (mirrors ARCA's ``migration_path`` ext key; ``engine_ext`` is
already inside the artifact). Per Model 1 that pinned artifact is later **handed to
baas and forwarded** to the external container (non-mount) — no ARCA mount chain.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Protocol

from agentclaw.community.core.config_compose.models import ComposeOccasion, ComposeRequest
from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.core.service_bot.services.deploy.engine_ext_stage import (
    enrich_engine_ext,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.kernel.bot_config import BotConfigArtifact


class ConfigComposerLike(Protocol):
    """The compose dependency this producer needs (satisfied by ``ConfigComposer``).

    Mirrors the real ``ConfigComposer.compose`` signature exactly — it takes a
    :class:`ComposeRequest` (NOT a bot dict). The producer is the adapter that
    translates the publish-flow ``bot`` row into that request (see
    :meth:`ExternalComposeProducer._compose_request`).
    """

    def compose(self, req: ComposeRequest) -> BotConfigArtifact: ...


class ExternalComposeProducer(DeployArtifactProducer):
    """Compose-from-DB+NAS build producer (base for engine-specific subclasses)."""

    requires_runtime_layout_observation = False

    def __init__(self, composer: ConfigComposerLike) -> None:
        self._composer = composer

    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        """Produce the deployable artifact: compose (+ inject ``engine_ext``) and pin
        the composed dict onto ``ext.config_artifact``.

        ``request.compose_occasion`` is what the compose is for (W8): a
        publish build is a runtime compose and leaves every category the
        engine's; eager provisioning passes ``PROVISION`` so the first
        artifact of a bot that carries a manifest says the platform owns them.

        The artifact is **refs-only** ({store, path} pointers; bytes persisted
        separately), so there's nothing to materialize and no fingerprint to keep —
        we pin the dict straight onto ``ext`` (mirrors ARCA's ``migration_path`` key).
        The verify/online publish stages — and eager teclaw provisioning — read
        ``ext.config_artifact`` and hand it to ``create_teclaw_bot`` (non-mount).
        """
        artifact = self._compose_with_engine_ext(
            dict(request.bot), request.version, request.compose_occasion
        )
        return DeployArtifact(success=True, ext={"config_artifact": artifact.to_dict()})

    def _compose_with_engine_ext(
        self,
        bot: dict[str, Any],
        version: int | None,
        occasion: ComposeOccasion = ComposeOccasion.RUNTIME,
    ) -> BotConfigArtifact:
        """Compose the artifact and inject the (producer-owned) opaque ``engine_ext``.

        The composer is engine-agnostic and speaks :class:`ComposeRequest`; this
        producer adapts the publish-flow ``bot`` row into that request
        (:meth:`_compose_request`) before composing. The engine's opaque payload is
        fetched here at build and frozen into the version — carried verbatim, never
        interpreted.
        """
        artifact = self._composer.compose(self._compose_request(bot, version, occasion))
        # Enrich the engine's opaque payload with the backend-owned identity + stage
        # keys. ``bot_id`` / ``owner_id`` / ``bot_name`` come from the same bot-row
        # fields ``_compose_request`` reads; build is always the draft stage
        # (verify/online re-stamp later). Shared with the runtime device-sync plugin
        # via the helper.
        engine_ext = enrich_engine_ext(
            self._fetch_engine_ext(bot),
            bot_id=bot.get("bot_id", ""),
            owner_id=bot.get("owner_id", ""),
            bot_name=bot.get("bot_name", ""),
            stage=PublishStage.DRAFT,
        )
        return dataclasses.replace(artifact, engine_ext=engine_ext)

    @staticmethod
    def _compose_request(
        bot: dict[str, Any], version: int | None, occasion: ComposeOccasion
    ) -> ComposeRequest:
        """Adapt the publish-flow ``bot`` row → a ``ComposeRequest`` for the composer.

        ``bot`` is the ``BotService.get_bot`` row (an ``ac_bots`` record), so the
        field mapping mirrors the runtime-delivery call sites
        (``teclaw_provision_service`` / ``teclaw_device_sync``): the bot's
        ``owner_id`` is the compose ``user_id``, ``active_engine`` is the engine,
        and ``version`` is the per-bot publish snapshot version being built.
        """
        return ComposeRequest(
            entity_id=bot.get("entity_id", ""),
            bot_id=str(bot.get("bot_id", "")),
            user_id=bot.get("owner_id", ""),
            engine_type=bot.get("active_engine", ""),
            entity_type=bot.get("entity_type", "staff"),
            version=version,
            occasion=occasion,
        )

    def _fetch_engine_ext(self, bot: dict[str, Any]) -> dict[str, Any]:
        """Opaque, engine-owned payload. Base: none. Subclasses (teclaw) override."""
        return {}
