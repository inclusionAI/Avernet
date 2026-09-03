"""``ArcaSnapshotProducer`` — produce one versioned ARCA build snapshot.

Wraps ``bot_build_service.build()`` (host-side rsync of the engine root into the
versioned target + regenerated ``mcporter.json``) and maps its result dict onto
a :class:`DeployArtifact`. The exact deployable pointers the build phase pins
(``migration_path`` / ``build_target_path``) are carried through unchanged on
``ext`` so the downstream verify/online deploy path — which reads them off
``BotPublishRecord.ext`` — is untouched.

This is the ARCA branch of the provider-keyed producer selection wired in
Task 12; external bots take :class:`TeclawComposeProducer` instead.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    CapturedServiceSkillsLayout,
    ServiceSkillsManifestBuilder,
    ServiceSkillsManifestError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.service_bot.services.bot_build_service import (
        BotBuildService,
    )


class ArcaSnapshotProducer(DeployArtifactProducer):
    """Wraps the existing ARCA build (rsync + mcporter snapshot), behavior-equivalent."""

    requires_runtime_layout_observation = True

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

    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        """Delegate to ``build()`` and map its result onto :class:`DeployArtifact`.

        The build phase used to read ``success`` / ``migration_path`` /
        ``build_target_path`` straight off ``build()``'s dict and pin the two
        paths onto ``ext`` (``publish_flow_service.py:291-300``). We reproduce
        exactly that: same keys, same values, same failure message — only the
        return type changes.
        """
        if request.version is None:
            raise ValueError("ARCA snapshot build requires a publish version")
        bot = dict(request.bot)
        captured_layout = self._skills_manifest_builder.capture(
            bot=bot,
            layout_observation=request.layout_observation,
        )
        build_kwargs: dict[str, Any] = {"bot": bot, "version": request.version}
        if captured_layout is not None:
            build_kwargs["shared_corpora"] = captured_layout.shared_corpora
            build_kwargs["active_runtime_path"] = captured_layout.active_runtime_path
        result = self._build_service.build(**build_kwargs)

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
            self._validate_shared_corpus_snapshot(
                captured=captured_layout,
                build_target_path=str(build_target_path),
                snapshot_paths=result.get("shared_corpus_snapshot_paths"),
                active_snapshot_path=result.get("active_skill_snapshot_path"),
            )
            # This manifest only describes the Skills slice frozen inside the
            # service version. It augments — never replaces — build_target_path.
            ext["skills_manifest"] = self._skills_manifest_builder.finalize(
                captured=captured_layout,
                bot=bot,
            )

        return DeployArtifact(
            success=success,
            ext=ext,
            message="" if success else "构建失败",
        )

    @staticmethod
    def _validate_shared_corpus_snapshot(
        *,
        captured: CapturedServiceSkillsLayout,
        build_target_path: str,
        snapshot_paths: object,
        active_snapshot_path: object,
    ) -> None:
        """Validate exclusions and exact Center links without dereferencing corpora."""

        if not captured.shared_corpora:
            return
        if (
            not isinstance(snapshot_paths, list)
            or len(snapshot_paths) != len(captured.shared_corpora)
            or any(not isinstance(item, str) for item in snapshot_paths)
        ):
            raise ServiceSkillsManifestError(
                "shared corpus snapshot is missing resolved exclusions"
            )
        root = Path(build_target_path)
        if not isinstance(active_snapshot_path, str):
            raise ServiceSkillsManifestError(
                "shared corpus snapshot is missing the active Skills root"
            )
        active_path = PurePosixPath(active_snapshot_path)
        if (
            active_path.is_absolute()
            or active_path.as_posix() != active_snapshot_path
            or any(part in {"", ".", ".."} for part in active_path.parts)
        ):
            raise ServiceSkillsManifestError(
                "shared corpus snapshot has an invalid active Skills root"
            )
        for relative in snapshot_paths:
            path = PurePosixPath(relative)
            if (
                path.is_absolute()
                or path.as_posix() != relative
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise ServiceSkillsManifestError(
                    "Center snapshot contains an invalid corpus exclusion"
                )
            copied = root.joinpath(*path.parts)
            if copied.exists() or copied.is_symlink():
                raise ServiceSkillsManifestError(
                    "Center snapshot must not copy the shared corpus"
                )

        center_delivery = next(
            delivery
            for delivery in captured.shared_corpora
            if delivery.corpus == "center"
        )
        center_root = PurePosixPath(center_delivery.runtime_path)
        expected = {
            (
                str(active_path / item["runtime_name"]),
                str(center_root / item["skill_uuid"] / item["sc_version_number"]),
            )
            for item in captured.center_skills
        }
        actual: list[tuple[str, str]] = []
        if root.exists():
            for entry in root.rglob("*"):
                if not entry.is_symlink():
                    continue
                target = os.readlink(entry)
                target_path = PurePosixPath(target)
                try:
                    target_path.relative_to(center_root)
                except ValueError:
                    continue
                actual.append(
                    (entry.relative_to(root).as_posix(), target_path.as_posix())
                )
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ServiceSkillsManifestError(
                "Center links do not match the frozen exact manifest"
            )
