"""``ArcaSnapshotProducer`` — produce the ARCA build snapshot (existing behavior).

Wraps the existing ``bot_build_service.build()`` (rsync engine dir into the
versioned target + regenerate ``mcporter.json``) and maps its result dict onto a
:class:`DeployArtifact` with **no behavior change**. The exact deployable
pointers the build phase pins today (``migration_path`` / ``build_target_path``)
are carried through unchanged on ``ext`` so the downstream verify/online deploy
path — which reads them off ``BotPublishRecord.ext`` — is untouched.

This is the ARCA branch of the provider-keyed producer selection wired in
Task 12; external bots take :class:`TeclawComposeProducer` instead.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    ServiceSkillsManifestBuilder,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService


class ArcaSnapshotProducer(DeployArtifactProducer):
    """Wraps the existing ARCA build (rsync + mcporter snapshot), behavior-equivalent."""

    def __init__(
        self,
        build_service: "BotBuildService",
        skills_manifest_builder: ServiceSkillsManifestBuilder,
    ) -> None:
        # Duck-typed at runtime — anything exposing ``build(bot, version) -> dict``
        # works (tests inject a lightweight stub). Annotated as the concrete
        # BotBuildService since that's what the DI root injects.
        self._build_service = build_service
        self._skills_manifest_builder = skills_manifest_builder

    def produce_artifact(self, bot: dict[str, Any], version: int) -> DeployArtifact:
        """Delegate to ``build()`` and map its result onto :class:`DeployArtifact`.

        The build phase used to read ``success`` / ``migration_path`` /
        ``build_target_path`` straight off ``build()``'s dict and pin the two
        paths onto ``ext`` (``publish_flow_service.py:291-300``). We reproduce
        exactly that: same keys, same values, same failure message — only the
        return type changes.
        """
        captured_layout = self._skills_manifest_builder.capture(bot=bot)
        result = self._build_service.build(bot=bot, version=version)

        success = bool(result.get("success"))
        ext: dict[str, Any] = {}
        # Carry through only the deployable pointers the build phase pins today,
        # and only when present — keep the pinned ext byte-for-byte equivalent.
        if "migration_path" in result:
            ext["migration_path"] = result.get("migration_path")
        if "build_target_path" in result:
            ext["build_target_path"] = result.get("build_target_path")
        if success and captured_layout is not None:
            build_target_path = result.get("build_target_path")
            if not build_target_path:
                raise ValueError(
                    "successful service build is missing build_target_path"
                )
            # This manifest only describes the Skills slice frozen inside the
            # full build target. It augments — never replaces — build_target_path.
            ext["skills_manifest"] = self._skills_manifest_builder.finalize(
                captured=captured_layout,
                build_target_path=str(build_target_path),
            )

        return DeployArtifact(
            success=success,
            ext=ext,
            message="" if success else "构建失败",
        )
