"""Published-only Version and consumable workshop query service."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from injector import inject

from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillVersionReadRepository,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.errors import (
    DraftFileNotFoundError,
    DraftFileNotTextError,
)
from agentclaw.community.core.skill_center.space_skill_version_query_service_protocol import (
    ConsumableSpaceSkillSummaryRecord,
    PublishedSkillFileContentRecord,
    PublishedSkillFileTreeRecord,
    PublishedSkillVersionRecord,
    SpaceSkillVersionQueryServiceProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.utils.env_utils import get_current_env


class SpaceSkillVersionQueryService(SpaceSkillVersionQueryServiceProtocol):
    @inject
    def __init__(
        self,
        access: SpaceAccessServiceProtocol,
        repository: SpaceSkillVersionReadRepository,
        canonical_store: CanonicalCenterVersionStore,
    ) -> None:
        self._access = access
        self._repository = repository
        self._canonical = canonical_store

    def list_versions(
        self, *, space_id: int, skill_id: int, actor_id: str, page: int, page_size: int
    ) -> tuple[int, list[PublishedSkillVersionRecord]]:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        total, rows = self._repository.list_published(
            space_id=space_id,
            skill_id=skill_id,
            env=get_current_env(),
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return total, [self._version(row) for row in rows]

    def get_version(
        self, *, space_id: int, skill_id: int, version: int, actor_id: str
    ) -> PublishedSkillVersionRecord:
        row = self._row(
            space_id=space_id, skill_id=skill_id, version=version, actor_id=actor_id
        )
        return self._version(row)

    def get_version_file_tree(
        self, *, space_id: int, skill_id: int, version: int, actor_id: str
    ) -> PublishedSkillFileTreeRecord:
        row, content = self._content(
            space_id=space_id, skill_id=skill_id, version=version, actor_id=actor_id
        )
        return {
            "version": row["version_ordinal"],
            "files": [
                {"path": item.path, "size": len(item.content)} for item in content.files
            ],
        }

    def read_version_file(
        self,
        *,
        space_id: int,
        skill_id: int,
        version: int,
        actor_id: str,
        path: str,
    ) -> PublishedSkillFileContentRecord:
        row, content = self._content(
            space_id=space_id, skill_id=skill_id, version=version, actor_id=actor_id
        )
        path = self._path(path)
        files = content.file_map
        if path not in files:
            raise DraftFileNotFoundError("published version file not found")
        try:
            text = files[path].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DraftFileNotTextError("published version file is not UTF-8") from exc
        return {"version": row["version_ordinal"], "path": path, "content": text}

    def list_consumable(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[ConsumableSpaceSkillSummaryRecord]]:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        keyword = keyword.strip() if keyword and keyword.strip() else None
        total, candidates = self._repository.list_consumable_candidates(
            space_id=space_id,
            env=get_current_env(),
            keyword=keyword,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        items = [
            {
                "skill_id": str(row["skill_id"]),
                "name": row["name"],
                "description": row["description"],
                "latest_published_version": {
                    "version": row["version_ordinal"],
                    "sc_version_number": row["sc_version_number"],
                    "published_at": row["published_at"],
                },
            }
            for row in candidates
        ]
        return total, items

    def _row(self, *, space_id: int, skill_id: int, version: int, actor_id: str):
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.get_published_ordinal(
            space_id=space_id,
            skill_id=skill_id,
            version=version,
            env=get_current_env(),
        )

    def _content(self, **kwargs):
        row = self._row(**kwargs)
        return row, self._canonical.read_version(self._ref(row))

    @staticmethod
    def _ref(row) -> CanonicalCenterVersionRef:
        return CanonicalCenterVersionRef(
            CanonicalCenterVersionIdentity(
                skill_uuid=row["skill_uuid"],
                sc_version_number=row["sc_version_number"],
            )
        )

    @staticmethod
    def _version(row) -> PublishedSkillVersionRecord:
        dependencies: list[str] = []
        if row["metadata_json"]:
            metadata = json.loads(row["metadata_json"])
            raw = metadata.get("mcp_dependencies", [])
            if not isinstance(raw, list) or any(
                not isinstance(item, str) for item in raw
            ):
                raise ValueError("Published Version MCP metadata is invalid")
            dependencies = raw
        return {
            "version": row["version_ordinal"],
            "sc_version_number": row["sc_version_number"],
            "name": row["name"],
            "description": row["description"],
            "mcp_dependencies": dependencies,
            "published_at": row["published_at"],
        }

    @staticmethod
    def _path(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value.startswith("/")
            or "\\" in value
        ):
            raise DraftFileNotFoundError("published version file not found")
        path = PurePosixPath(value)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise DraftFileNotFoundError("published version file not found")
        return path.as_posix()
