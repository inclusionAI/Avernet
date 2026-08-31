"""Resolve Center assets to exact immutable PUBLISHED Versions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import json

from injector import inject

from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SkillVersionRecord,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.version_resolution_contract import (
    PublishedSkillVersion,
    SkillVersionResolutionError,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


class SkillVersionResolver:
    """Pure DB-read module: no SC, Store, Installation, or Runtime side effects."""

    @inject
    def __init__(self, versions: SkillVersionRepositoryProtocol) -> None:
        self._versions = versions

    def resolve_latest_runtime_assets(
        self,
        *,
        env: str,
        assets: Sequence[RegisteredSkillAsset],
    ) -> tuple[RegisteredSkillAsset, ...]:
        stable_assets = tuple(assets)
        center_ids = tuple(
            dict.fromkeys(
                asset.skill_id
                for asset in stable_assets
                if asset.git_path.startswith("center://")
            )
        )
        if not center_ids:
            return stable_assets
        rows = self._versions.list_latest_published(env=env, skill_ids=center_ids)
        versions_by_skill_id = {int(row["skill_id"]): row for row in rows}
        if len(versions_by_skill_id) != len(center_ids):
            raise SkillVersionResolutionError("Center Skill has no PUBLISHED Version")

        resolved: list[RegisteredSkillAsset] = []
        for asset in stable_assets:
            if not asset.git_path.startswith("center://"):
                resolved.append(asset)
                continue
            if not isinstance(asset.skill_uuid, str) or not asset.skill_uuid:
                raise SkillVersionResolutionError(
                    "Center Skill has no stable skill_uuid"
                )
            version = self._published(versions_by_skill_id[asset.skill_id])
            resolved.append(
                replace(
                    asset,
                    sc_version_number=version.sc_version_number,
                    mcp_dependencies=version.mcp_dependencies,
                )
            )
        return tuple(resolved)

    def resolve_exact_published(
        self,
        *,
        env: str,
        skill_id: int,
        skill_version_id: int,
    ) -> PublishedSkillVersion:
        row = self._versions.get_exact_published(
            env=env,
            skill_id=skill_id,
            skill_version_id=skill_version_id,
        )
        if row is None:
            raise SkillVersionResolutionError(
                "Addressed Skill Version is not PUBLISHED"
            )
        return self._published(row)

    @staticmethod
    def _published(row: SkillVersionRecord) -> PublishedSkillVersion:
        number = row["sc_version_number"]
        if not isinstance(number, str) or not number:
            raise SkillVersionResolutionError(
                "PUBLISHED Skill Version has no SC version number"
            )
        metadata = SkillVersionResolver._metadata(row["metadata_json"])
        if "mcp_dependencies" not in metadata:
            raise SkillVersionResolutionError(
                "Skill Version has no materialized MCP dependency metadata"
            )
        dependencies = metadata["mcp_dependencies"]
        if not isinstance(dependencies, list):
            raise SkillVersionResolutionError(
                "Skill Version MCP dependencies must be a list"
            )
        try:
            dependency_codes = mcp_dependency_codes(dependencies)
        except ValueError as exc:
            raise SkillVersionResolutionError(
                "Skill Version has invalid MCP dependencies"
            ) from exc
        if any(not code for code in dependency_codes):
            raise SkillVersionResolutionError(
                "Skill Version has an empty MCP dependency code"
            )
        name = row["name"]
        if not isinstance(name, str) or not name:
            raise SkillVersionResolutionError("Skill Version has no name")
        return PublishedSkillVersion(
            skill_version_id=int(row["id"]),
            skill_id=int(row["skill_id"]),
            version_ordinal=int(row["version_ordinal"]),
            sc_version_number=number,
            sc_skill_id=row["sc_skill_id"],
            sc_version_id=row["sc_version_id"],
            name=name,
            description=row["description"],
            mcp_dependencies=tuple(dependencies),
            published_at=row["published_at"],
        )

    @staticmethod
    def _metadata(raw: str | None) -> Mapping[str, object]:
        if raw is None:
            raise SkillVersionResolutionError(
                "PUBLISHED Skill Version has no materialized metadata"
            )
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SkillVersionResolutionError(
                "Skill Version metadata is not valid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise SkillVersionResolutionError(
                "Skill Version metadata must be an object"
            )
        return parsed


__all__ = ["SkillVersionResolver"]
