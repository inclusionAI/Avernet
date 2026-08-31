"""Pure exact-Center reference parser shared by build admission and lineage."""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    validate_service_skills_manifest_for_release,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.kernel.bot_config import BotConfigArtifact, SCHEMA_VERSION


@dataclass(frozen=True, order=True, slots=True)
class ExactCenterArtifactRef:
    skill_uuid: str
    sc_version_number: str


def exact_center_refs_from_artifact_ext(
    ext: dict,
    *,
    validate_full_artifact: bool = True,
) -> tuple[ExactCenterArtifactRef, ...]:
    """Parse both file-manifest v1 and Teclaw artifact v1-v4 shapes.

    Old file artifacts without ``skills_manifest`` are valid and return no
    Center refs.  Any present-but-invalid metadata raises so the lineage reader
    can report UNKNOWN and the build commit can fail closed.
    """

    if not isinstance(ext, dict):
        raise ValueError("artifact ext must be an object")
    refs: set[ExactCenterArtifactRef] = set()

    manifest = ext.get("skills_manifest")
    if manifest is not None:
        if not isinstance(manifest, dict):
            raise ValueError("skills_manifest must be an object")
        validate_service_skills_manifest_for_release(
            manifest,
            {"active_engine": manifest.get("engine")},
        )
        for item in manifest.get("center_skills") or ():
            identity = CanonicalCenterVersionIdentity(
                skill_uuid=item["skill_uuid"],
                sc_version_number=item["sc_version_number"],
            )
            refs.add(
                ExactCenterArtifactRef(
                    skill_uuid=identity.skill_uuid,
                    sc_version_number=identity.sc_version_number,
                )
            )

    raw_artifact = ext.get("config_artifact")
    if raw_artifact is not None:
        if not isinstance(raw_artifact, dict):
            raise ValueError("config_artifact must be an object")
        if not validate_full_artifact and "skills" not in raw_artifact:
            return tuple(sorted(refs))
        artifact = BotConfigArtifact.from_dict(raw_artifact)
        if (
            not isinstance(artifact.schema_version, int)
            or artifact.schema_version < 1
            or artifact.schema_version > SCHEMA_VERSION
        ):
            raise ValueError("unsupported config artifact schema")
        for skill in artifact.skills:
            if skill.store != "skill-center":
                continue
            if skill.store not in artifact.stores:
                raise ValueError("skill-center ref has no Store")
            parts = skill.path.split("/")
            if len(parts) != 2:
                raise ValueError("skill-center path must be uuid/version")
            identity = CanonicalCenterVersionIdentity(
                skill_uuid=parts[0],
                sc_version_number=parts[1],
            )
            refs.add(
                ExactCenterArtifactRef(
                    skill_uuid=identity.skill_uuid,
                    sc_version_number=identity.sc_version_number,
                )
            )

    return tuple(sorted(refs))


__all__ = ["ExactCenterArtifactRef", "exact_center_refs_from_artifact_ext"]
