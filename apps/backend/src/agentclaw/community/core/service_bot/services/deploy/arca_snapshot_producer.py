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
        if success and "publish_ignore" in result:
            ext["publish_ignore"] = result["publish_ignore"]
        if success and result.get("active_skill_snapshot_path"):
            ext["active_skill_snapshot_path"] = result["active_skill_snapshot_path"]
        if success and captured_layout is not None:
            build_target_path = result.get("build_target_path")
            if not build_target_path:
                raise ValueError(
                    "successful service build is missing build_target_path"
                )
            delivered_center_skills = self._validate_shared_corpus_snapshot(
                captured=captured_layout,
                build_target_path=str(build_target_path),
                snapshot_paths=result.get("shared_corpus_snapshot_paths"),
                active_snapshot_path=result.get("active_skill_snapshot_path"),
            )
            # This manifest only describes the Skills slice frozen inside the
            # service version. It augments — never replaces — build_target_path.
            ext["skills_manifest"] = self._skills_manifest_builder.finalize(
                captured=captured_layout,
                delivered_center_skills=delivered_center_skills,
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
    ) -> tuple[dict[str, Any], ...]:
        """Validate corpus exclusions and return the Center versions delivered by the snapshot."""

        if not captured.shared_corpora:
            return ()
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
            not active_path.parts
            or active_path.is_absolute()
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
        active = root.joinpath(*active_path.parts)
        if any(
            root.joinpath(*active_path.parts[:depth]).is_symlink()
            for depth in range(1, len(active_path.parts) + 1)
        ):
            raise ServiceSkillsManifestError("shared corpus snapshot has an invalid active Skills root")

        delivered = []
        expected = set()
        for item in captured.center_skills:
            name = item["runtime_name"]
            if ArcaSnapshotProducer._is_self_contained_skill(active / name):
                continue
            delivered.append(item)
            expected.add(
                (str(active_path / name), str(center_root / item["skill_uuid"] / item["sc_version_number"]))
            )
        actual: list[tuple[str, str]] = []
        assert captured.active_runtime_path is not None
        runtime_active = PurePosixPath(captured.active_runtime_path)
        if root.exists():
            for entry in root.rglob("*"):
                if not entry.is_symlink():
                    continue
                target = os.readlink(entry)
                target_path = PurePosixPath(target)
                if not target_path.is_absolute() and entry.is_relative_to(active):
                    target_path = runtime_active / entry.relative_to(active).parent / target_path
                if PurePosixPath(os.path.normpath(target_path)).is_relative_to(center_root):
                    actual.append((entry.relative_to(root).as_posix(), target))
        actual_links = set(actual)
        if len(actual) != len(actual_links) or actual_links != expected:
            paths = ", ".join(sorted({path for path, _ in actual_links ^ expected}))
            raise ServiceSkillsManifestError(
                f"Center links do not match the frozen exact manifest: {paths}"
            )
        return tuple(delivered)

    @staticmethod
    def _is_self_contained_skill(directory: Path) -> bool:
        if directory.is_symlink() or not directory.is_dir():
            return False
        manifest = directory / "SKILL.md"
        if manifest.is_symlink() or not manifest.is_file():
            return False
        try:
            with manifest.open("rb") as stream:
                if not stream.read(1):
                    return False
        except OSError:
            return False
        package_root = directory.resolve()
        try:
            for parent, _, names in directory.walk(on_error=_raise_walk_error):
                for name in names:
                    entry = parent / name
                    if not entry.is_symlink():
                        continue
                    target = Path(os.readlink(entry))
                    if target.is_absolute() or not Path(
                        os.path.normpath(parent / target)
                    ).is_relative_to(directory):
                        return False
                    candidate = parent
                    for part in target.parts:
                        candidate /= part
                        if not candidate.resolve(strict=True).is_relative_to(package_root):
                            return False
        except (OSError, RuntimeError):
            return False
        return True


def _raise_walk_error(error: OSError) -> None:
    raise error
