"""``LocalBuildProducer`` — produce a build snapshot from a local engine source.

The singlebox / local-dev counterpart of :class:`ArcaSnapshotProducer`. It still
delegates to ``bot_build_service.build()`` (host-side copy of the engine root
into the versioned target), but the **source** is the engine's local working
directory (``EngineSandboxProvider.get_base_path()``, e.g. ``~/.openclaw`` for
singlebox) instead of the production per-bot NAS merge area (which only
exists on the ARCA/ECS sandbox and is never writable/executable from a dev host).

Why a separate producer:
- The ARCA build path assumes a per-bot NFS mount that only exists on the
  production ARCA/ECS sandbox. A developer laptop running singlebox has the
  engine's real files under the configured engine root (``openclaw_root``),
  never under the NAS merge path — so the NAS migration
  (``get_bot_nas_dir`` + ``sudo chmod`` + ``sudo rsync``) aborts on the host
  (``sudo: a password is required``) and the publish build stage fails.
- ``BotBuildService.build()`` already accepts an optional
  ``local_source_root`` keyword that, when set, replaces the NAS source and
  disables the ``sudo chmod`` / ``sudo rsync`` privilege dance. This producer
  is the thin layer that supplies that root from the matching engine sandbox
  provider.

Behavior parity with ARCA:
- Same ``requires_runtime_layout_observation = True`` so ``BuildStageRunner``
  probes the runtime layout (and the shared-corpus exclusions are honored).
- ``build()`` returns the same result dict (``migration_path`` /
  ``build_target_path`` / ``success``); the ext mapping is byte-for-byte
  identical to :class:`ArcaSnapshotProducer` so the downstream verify/online
  deploy path is untouched.

Selected via ``DeployArtifactProducerRouter`` under the ``"local"`` key, and —
because ``resolve()`` returns the *hit* provider (it does not fall back to the
default once a key matches) — singlebox re-points the ``"arca"``/``"baas"``
keys here too, so bots whose ``device_provider`` resolves to ``"baas"`` still
land on the local snapshot path instead of the NAS one.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    ServiceSkillsManifestBuilder,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.service_bot.services.bot_build_service import (
        BotBuildService,
    )


class LocalBuildProducer(DeployArtifactProducer):
    """Build a snapshot from the engine's local working directory (no NAS).

    Wired for singlebox / local dev so the publish build stage never touches
    the production NAS merge area, mirroring how the rest of singlebox keeps
    everything on the localhost filesystem.
    """

    requires_runtime_layout_observation = True

    def __init__(
        self,
        build_service: "BotBuildService",
        skills_manifest_builder: ServiceSkillsManifestBuilder,
        sandbox_registry: EngineSandboxRegistry,
    ) -> None:
        self._build_service = build_service
        self._skills_manifest_builder = skills_manifest_builder
        self._sandbox_registry = sandbox_registry

    def _resolve_local_source_root(self, bot: dict[str, Any]) -> Path:
        """Resolve the local engine source root for ``bot``.

        Uses the same engine resolution as ``BotBuildService.build()`` so the
        selected provider matches the build plan: fall back through
        ``active_engine`` → resolved engine → the default engine when the
        bot's engine is unknown (mirrors
        ``BotBuildService._resolve_sandbox_provider``'s fallback chain).
        """
        from agentclaw.community.core.bot_management.engines.registry import (
            resolve_bot_engine,
        )
        from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

        engine_type = bot.get("active_engine") or DEFAULT_ENGINE_TYPE
        engine_type = resolve_bot_engine(bot) or engine_type
        try:
            provider = self._sandbox_registry.resolve(engine_type)
        except Exception:
            try:
                provider = self._sandbox_registry.resolve(DEFAULT_ENGINE_TYPE)
            except Exception as exc:  # pragma: no cover - registry is non-empty
                raise ValueError(
                    "no engine sandbox provider available for local build"
                ) from exc
        return Path(provider.get_base_path())

    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        """Delegate to ``build()`` with the local engine source root.

        Same ext mapping as :class:`ArcaSnapshotProducer` — only the source
        root changes — so the deployable pointers pinned onto ``ext`` stay
        identical and the downstream verify/online deploy path is untouched.
        """
        if request.version is None:
            raise ValueError("local build snapshot requires a publish version")
        bot = dict(request.bot)
        captured_layout = self._skills_manifest_builder.capture(
            bot=bot,
            layout_observation=request.layout_observation,
        )
        build_kwargs: dict[str, Any] = {
            "bot": bot,
            "version": request.version,
            "local_source_root": self._resolve_local_source_root(bot),
        }
        if captured_layout is not None:
            build_kwargs["shared_corpora"] = captured_layout.shared_corpora
            build_kwargs["active_runtime_path"] = captured_layout.active_runtime_path
        result = self._build_service.build(**build_kwargs)

        success = bool(result.get("success"))
        ext: dict[str, Any] = {}
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
            # Reuse the ARCA shared-corpus validation verbatim: the snapshot
            # shape (exclusions + exact Center links) is engine-determined, not
            # NAS/local-determined, so the same invariants apply to a local build.
            from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
                ArcaSnapshotProducer,
            )

            ArcaSnapshotProducer._validate_shared_corpus_snapshot(
                captured=captured_layout,
                build_target_path=str(build_target_path),
                snapshot_paths=result.get("shared_corpus_snapshot_paths"),
                active_snapshot_path=result.get("active_skill_snapshot_path"),
            )
            ext["skills_manifest"] = self._skills_manifest_builder.finalize(
                captured=captured_layout,
                bot=bot,
            )

        return DeployArtifact(
            success=success,
            ext=ext,
            message="" if success else "构建失败",
        )
