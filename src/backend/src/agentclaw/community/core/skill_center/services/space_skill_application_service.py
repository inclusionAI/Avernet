"""Application orchestration for Space Skill Draft creation and editing."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from uuid import uuid4

from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository,
)
from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
    DraftRevisionIdentity,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.errors import (
    SpaceSkillIdempotencyConflictError,
)
from agentclaw.community.core.skill_center.git_snapshot import (
    GitSnapshotServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skill_center.space_skill_application_service_protocol import (
    SpaceSkillApplicationServiceProtocol,
    SpaceSkillCreationOutcome,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol


logger = logging.getLogger(__name__)


class SpaceSkillApplicationService(SpaceSkillApplicationServiceProtocol):
    def __init__(
        self,
        *,
        access: SpaceAccessServiceProtocol,
        repository: SpaceSkillRepository,
        package_validator: SkillPackageValidator,
        draft_store: DraftContentStore,
        git_snapshots: GitSnapshotServiceProtocol,
        env_provider: Callable[[], str],
        tenant_provider: Callable[[], str],
    ) -> None:
        self._access = access
        self._repository = repository
        self._validator = package_validator
        self._draft_store = draft_store
        self._git_snapshots = git_snapshots
        self._env_provider = env_provider
        self._tenant_provider = tenant_provider

    def create_from_folder(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        files: Sequence[tuple[str, bytes]],
    ) -> SpaceSkillCreationOutcome:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        package = self._validator.validate_directory(files)
        request_id = self._request_id(request_id)
        request_hash = hashlib.sha256(
            b"FOLDER\0"
            + str(space_id).encode("ascii")
            + b"\0"
            + package.canonical_zip
        ).hexdigest()
        replay = self._creation_replay(
            space_id=space_id, request_id=request_id, request_hash=request_hash
        )
        if replay is not None:
            return replay
        return self._persist_creation(
            space_id=space_id,
            actor_id=actor_id,
            request_id=request_id,
            request_hash=request_hash,
            package=package,
            source_data={
                "draft_source_kind": "FOLDER",
                "source_type": "FOLDER",
            },
        )

    def create_from_git(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        git_url: str,
        branch: str | None,
        subdir: str | None,
    ) -> SpaceSkillCreationOutcome:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        request_id = self._request_id(request_id)
        request_hash = hashlib.sha256(
            "\0".join(
                ("GIT", str(space_id), git_url.strip(), branch or "", subdir or "")
            ).encode("utf-8")
        ).hexdigest()
        replay = self._creation_replay(
            space_id=space_id, request_id=request_id, request_hash=request_hash
        )
        if replay is not None:
            return replay
        snapshot = self._git_snapshots.fetch(
            git_url=git_url, branch=branch, subdir=subdir
        )
        package = self._validator.validate_directory(snapshot.files)
        return self._persist_creation(
            space_id=space_id,
            actor_id=actor_id,
            request_id=request_id,
            request_hash=request_hash,
            package=package,
            source_data={
                "draft_source_kind": "GIT",
                "source_type": "GIT",
                "source_repo_url": snapshot.repo_url,
                "source_branch": snapshot.resolved_branch,
                "source_commit_sha": snapshot.commit_sha,
                "source_subdir": snapshot.source_subdir,
            },
        )

    def _creation_replay(
        self, *, space_id: int, request_id: str, request_hash: str
    ) -> SpaceSkillCreationOutcome | None:
        env = self._env_provider()
        existing = self._repository.get_creation_by_request_id(
            request_id=request_id, env=env
        )
        if existing is None:
            return None
        if (
            existing["space_id"] != space_id
            or existing["request_hash"] != request_hash
        ):
            raise SpaceSkillIdempotencyConflictError(
                "creation request already belongs to another intent"
            )
        return SpaceSkillCreationOutcome(skill_id=existing["skill_id"], created=False)

    def _persist_creation(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        request_hash: str,
        package,
        source_data: dict[str, str],
    ) -> SpaceSkillCreationOutcome:
        env = self._env_provider()

        skill_uuid = str(uuid4())
        revision = DraftRevisionIdentity(
            tenant=self._tenant_provider(),
            env=env,
            skill_uuid=skill_uuid,
            target_version=1,
            revision_id=str(uuid4()),
        )
        ref = self._draft_store.write_revision(revision, package)
        try:
            created = self._repository.create_space_skill(
                skill_data={
                    "name": package.name,
                    "description": None,
                    "env": env,
                    "skill_uuid": skill_uuid,
                    "zip_url": ref.locator,
                    "draft_target_version": 1,
                    "draft_status": "EDITING",
                    "draft_description": package.description,
                    "draft_source_kind": "FOLDER",
                    "creation_request_id": request_id,
                    "creation_request_hash": request_hash,
                    **source_data,
                },
                ownership_data={
                    "space_id": space_id,
                    "created_by": actor_id,
                    "env": env,
                },
                owner_grant_data={
                    "user_id": actor_id,
                    "granted_by": actor_id,
                    "env": env,
                },
            )
        except Exception:
            self._best_effort_delete(ref)
            raise
        if not created["created"]:
            self._best_effort_delete(ref)
        return SpaceSkillCreationOutcome(
            skill_id=created["skill"]["id"], created=created["created"]
        )

    @staticmethod
    def _request_id(value: str) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized or len(normalized) > 128:
            raise ValueError("Idempotency-Key must contain 1..128 characters")
        return normalized

    def _best_effort_delete(self, ref: DraftRevisionRef) -> None:
        try:
            self._draft_store.delete_revision(ref)
        except Exception:
            logger.exception("failed to clean uncommitted Space Skill Draft revision")
