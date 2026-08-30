"""Application orchestration for Space Skill Draft creation and editing."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from uuid import uuid4

from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionRepositoryProtocol,
    SpaceSkillDraftRepository,
    SpaceSkillRepository,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillDraftRecord,
)
from agentclaw.community.core.models.space_skill import DraftStatus
from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
    DraftRevisionIdentity,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.errors import (
    DraftFileNotFoundError,
    DraftFileNotTextError,
    DraftFrozenError,
    DraftNotFoundError,
    SkillNameChangedError,
    SpaceSkillIdempotencyConflictError,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageValidator,
    ValidatedSkillPackage,
)
from agentclaw.community.core.skill_center.space_skill_application_service_protocol import (
    DraftFileContent,
    DraftFileItem,
    DraftFileTree,
    DraftMutationResult,
    DraftDeleteOutcome,
    SpaceSkillApplicationServiceProtocol,
    SpaceSkillCreationOutcome,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownloadRequest,
    SkillCenterGateway,
    SkillCenterReadScope,
)
from agentclaw.community.plugin_api.space_skill_source import (
    SpaceSkillSourcePlugin,
)


logger = logging.getLogger(__name__)


class SpaceSkillApplicationService(SpaceSkillApplicationServiceProtocol):
    def __init__(
        self,
        *,
        access: SpaceAccessServiceProtocol,
        repository: SpaceSkillRepository,
        draft_repository: SpaceSkillDraftRepository,
        package_validator: SkillPackageValidator,
        draft_store: DraftContentStore,
        sources: SpaceSkillSourcePlugin,
        versions: SkillVersionRepositoryProtocol,
        canonical_store: CanonicalCenterVersionStore,
        skill_center: SkillCenterGateway,
        env_provider: Callable[[], str],
        tenant_provider: Callable[[], str],
    ) -> None:
        self._access = access
        self._repository = repository
        self._draft_repository = draft_repository
        self._validator = package_validator
        self._draft_store = draft_store
        self._sources = sources
        self._versions = versions
        self._canonical = canonical_store
        self._skill_center = skill_center
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
            b"FOLDER\0" + str(space_id).encode("ascii") + b"\0" + package.canonical_zip
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
        snapshot = self._sources.fetch_git_snapshot(
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

    def get_draft_file_tree(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftFileTree:
        record, ref, package = self._read_draft(
            space_id=space_id, skill_id=skill_id, actor_id=actor_id
        )
        del record
        return DraftFileTree(
            revision_id=ref.revision_id,
            files=tuple(
                DraftFileItem(path=path, size=len(content))
                for path, content in package.files
            ),
        )

    def read_draft_file(
        self, *, space_id: int, skill_id: int, actor_id: str, path: str
    ) -> DraftFileContent:
        _record, ref, package = self._read_draft(
            space_id=space_id, skill_id=skill_id, actor_id=actor_id
        )
        path = self._validator.normalize_relative_path(path)
        files = dict(package.files)
        if path not in files:
            raise DraftFileNotFoundError("draft file not found")
        try:
            content = files[path].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DraftFileNotTextError("draft file is not UTF-8 text") from exc
        return DraftFileContent(path=path, content=content, revision_id=ref.revision_id)

    def save_draft_file(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        path: str,
        content: str,
        expected_revision_id: str,
        fencing_token: int | None,
    ) -> DraftMutationResult:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        record = self._draft_repository.get_draft_for_mutation(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            expected_revision_id=expected_revision_id,
            fencing_token=fencing_token,
            env=self._env_provider(),
        )
        self._require_editing(record)
        package = self._read_draft_package(record)
        path = self._validator.normalize_relative_path(path)
        files = dict(package.files)
        files[path] = content.encode("utf-8")
        updated = self._validator.validate_directory(tuple(files.items()))
        self._require_stable_name(record, updated)
        return self._commit_draft_mutation(
            record=record,
            package=updated,
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            expected_revision_id=expected_revision_id,
            fencing_token=fencing_token,
        )

    def refresh_draft_from_git(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
    ) -> DraftMutationResult:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        record = self._draft_repository.get_draft_for_mutation(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            expected_revision_id=expected_revision_id,
            fencing_token=fencing_token,
            env=self._env_provider(),
        )
        self._require_editing(record)
        if record["source_kind"] != "GIT" or not record["source_repo_url"]:
            raise ValueError("Draft is not backed by a Git snapshot")
        snapshot = self._sources.fetch_git_snapshot(
            git_url=record["source_repo_url"],
            branch=record["source_branch"],
            subdir=record["source_subdir"],
        )
        package = self._validator.validate_directory(snapshot.files)
        self._require_stable_name(record, package)
        return self._commit_draft_mutation(
            record=record,
            package=package,
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            expected_revision_id=expected_revision_id,
            fencing_token=fencing_token,
            source_commit_sha=snapshot.commit_sha,
        )

    def delete_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
    ) -> DraftDeleteOutcome:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        result = self._draft_repository.delete_draft(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            expected_revision_id=expected_revision_id,
            fencing_token=fencing_token,
            env=self._env_provider(),
        )
        ref = DraftRevisionRef.from_locator(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            locator=result["locator"],
        )
        self._best_effort_delete(ref)
        return DraftDeleteOutcome(
            changed=result["changed"], deleted_scope=result["deleted_scope"]
        )

    def create_upgrade_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
    ) -> DraftMutationResult:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        request_id = self._request_id(request_id)
        identity = self._draft_repository.get_skill_for_upgrade(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )
        replay = self._draft_repository.get_upgrade_by_request_id(
            request_id=request_id, env=self._env_provider()
        )
        if replay is not None:
            if replay["skill_id"] != skill_id:
                raise SpaceSkillIdempotencyConflictError(
                    "upgrade request already belongs to another Skill"
                )
            return self._draft_result(replay)
        rows = self._versions.list_latest_published(
            env=self._env_provider(), skill_ids=(skill_id,)
        )
        if not rows:
            raise DraftNotFoundError("latest Published Version not found")
        latest = rows[0]
        exact_identity = CanonicalCenterVersionIdentity(
            skill_uuid=identity["skill_uuid"],
            sc_version_number=latest["sc_version_number"],
        )
        exact_ref = CanonicalCenterVersionRef(exact_identity)
        try:
            exact = self._canonical.read_version(exact_ref)
        except CanonicalCenterStoreError as exc:
            if exc.code not in {
                CanonicalCenterStoreErrorCode.NOT_READY,
                CanonicalCenterStoreErrorCode.CORRUPT_CONTENT,
            }:
                raise
            exact = self._repair_exact_version(identity, exact_identity)
        package = self._validator.validate_directory(
            tuple((item.path, item.content) for item in exact.files)
        )
        self._require_stable_name(
            {
                "name": identity["name"],
            },
            package,
        )
        target_version = latest["version_ordinal"] + 1
        revision = DraftRevisionIdentity(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            skill_uuid=identity["skill_uuid"],
            target_version=target_version,
            revision_id=str(uuid4()),
        )
        ref = self._draft_store.write_revision(revision, package)
        try:
            result = self._draft_repository.create_upgrade_draft(
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                request_id=request_id,
                expected_version_id=latest["id"],
                target_version=target_version,
                new_locator=ref.locator,
                new_description=package.description,
                env=self._env_provider(),
            )
        except Exception:
            self._best_effort_delete(ref)
            raise
        if not result["created"]:
            self._best_effort_delete(ref)
        return self._draft_result(result["draft"])

    def _repair_exact_version(self, identity, exact_identity):
        if identity["sc_team_id"] is None:
            raise RuntimeError("Space Skill has no SkillCenter team identity")
        download = self._skill_center.get_exact_download(
            SkillCenterExactDownloadRequest(
                skill_code=identity["skill_uuid"],
                version_number=exact_identity.sc_version_number,
                scope=SkillCenterReadScope.TEAM,
                team_id=str(identity["sc_team_id"]),
            )
        )
        package = self._validator.validate_zip(
            self._sources.fetch_exact_package(
                url=download.download_url, expected_sha256=download.sha256
            )
        )
        version = CanonicalCenterVersion.from_files(exact_identity, dict(package.files))
        self._canonical.write_version(version)
        return self._canonical.read_version(CanonicalCenterVersionRef(exact_identity))

    @staticmethod
    def _draft_result(record) -> DraftMutationResult:
        ref = record["locator"].rsplit("/", 1)[-1]
        return DraftMutationResult(
            target_version=record["target_version"],
            status=record["status"],
            revision_id=ref,
            name=record["name"],
            description=record["draft_description"],
            source_kind=record["source_kind"],
            source_repo_url=(
                record["source_repo_url"] if record["source_kind"] == "GIT" else None
            ),
            source_branch=(
                record["source_branch"] if record["source_kind"] == "GIT" else None
            ),
            source_commit_sha=(
                record["source_commit_sha"] if record["source_kind"] == "GIT" else None
            ),
            source_subdir=(
                record["source_subdir"] if record["source_kind"] == "GIT" else None
            ),
        )

    def _draft_record(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillDraftRecord:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._draft_repository.get_draft(
            space_id=space_id, skill_id=skill_id, env=self._env_provider()
        )

    def _read_draft(self, *, space_id: int, skill_id: int, actor_id: str):
        record = self._draft_record(
            space_id=space_id, skill_id=skill_id, actor_id=actor_id
        )
        ref = DraftRevisionRef.from_locator(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            locator=record["locator"],
        )
        return record, ref, self._draft_store.read_revision(ref)

    def _read_draft_package(self, record: SpaceSkillDraftRecord):
        ref = DraftRevisionRef.from_locator(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            locator=record["locator"],
        )
        return self._draft_store.read_revision(ref)

    @staticmethod
    def _require_editing(record: SpaceSkillDraftRecord) -> None:
        if record["status"] == DraftStatus.FROZEN:
            raise DraftFrozenError("draft is frozen")

    @staticmethod
    def _require_stable_name(
        record: SpaceSkillDraftRecord, package: ValidatedSkillPackage
    ) -> None:
        if package.name != record["name"]:
            raise SkillNameChangedError("SKILL.md name is immutable")

    def _commit_draft_mutation(
        self,
        *,
        record: SpaceSkillDraftRecord,
        package: ValidatedSkillPackage,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
        source_commit_sha: str | None = None,
    ) -> DraftMutationResult:
        identity = DraftRevisionIdentity(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            skill_uuid=record["skill_uuid"],
            target_version=record["target_version"],
            revision_id=str(uuid4()),
        )
        ref = self._draft_store.write_revision(identity, package)
        try:
            old_locator = self._draft_repository.replace_draft_revision(
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                expected_revision_id=expected_revision_id,
                fencing_token=fencing_token,
                new_locator=ref.locator,
                new_description=package.description,
                source_commit_sha=source_commit_sha,
                env=self._env_provider(),
            )
        except Exception:
            self._best_effort_delete(ref)
            raise
        old_ref = DraftRevisionRef.from_locator(
            tenant=self._tenant_provider(),
            env=self._env_provider(),
            locator=old_locator,
        )
        self._best_effort_delete(old_ref)
        return DraftMutationResult(
            target_version=record["target_version"],
            status="EDITING",
            revision_id=ref.revision_id,
            name=package.name,
            description=package.description,
            source_kind=record["source_kind"],
            source_repo_url=record["source_repo_url"],
            source_branch=record["source_branch"],
            source_commit_sha=source_commit_sha or record["source_commit_sha"],
            source_subdir=record["source_subdir"],
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
        if existing["space_id"] != space_id or existing["request_hash"] != request_hash:
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
