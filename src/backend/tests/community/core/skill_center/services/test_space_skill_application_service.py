"""Behavior tests for the Space Skill Draft application seam."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStoreError,
    DraftContentStoreErrorCode,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.plugin_api.skill_center_gateway import SkillCenterExactDownload
from agentclaw.community.core.skill_center.errors import (
    DraftFrozenError,
    DraftRevisionConflictError,
    SpaceSkillIdempotencyConflictError,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.space_skill_application_service import (
    SpaceSkillApplicationService,
)
from agentclaw.community.plugin_api.space_skill_source import GitSkillSnapshot
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.testing.draft_content_store import LocalDraftContentStore


def _folder(description: str = "Draft description") -> list[tuple[str, bytes]]:
    return [
        (
            "draft-skill/SKILL.md",
            (
                f"---\nname: draft-skill\ndescription: {description}\n---\n# Draft\n"
            ).encode(),
        ),
        ("draft-skill/references/example.md", b"example"),
    ]


def _service():
    access = MagicMock()
    access.require_space_member.return_value = (MagicMock(), MagicMock())
    repository = MagicMock()
    draft_repository = MagicMock()
    versions = MagicMock()
    canonical = MagicMock()
    gateway = MagicMock()
    sources = MagicMock()
    repository.get_creation_by_request_id.return_value = None
    repository.create_space_skill.return_value = {
        "created": True,
        "skill": {
            "id": 51,
            "skill_uuid": "11111111-1111-4111-8111-111111111111",
            "draft_target_version": 1,
            "draft_status": "EDITING",
            "env": "test",
        },
        "ownership": {"id": 61, "skill_id": 51, "space_id": 7, "env": "test"},
        "owner_grant": {
            "id": 71,
            "skill_id": 51,
            "user_id": "owner-1",
            "role": "OWNER",
            "status": "ACTIVE",
            "owner_slot": 1,
            "env": "test",
        },
    }
    validator = SkillPackageValidator(SkillParser())
    store = LocalDraftContentStore(validator)
    service = SpaceSkillApplicationService(
        access=access,
        repository=repository,
        draft_repository=draft_repository,
        package_validator=validator,
        draft_store=store,
        sources=sources,
        versions=versions,
        canonical_store=canonical,
        skill_center=gateway,
        env_provider=lambda: "test",
        tenant_provider=lambda: "tenant-a",
    )
    return (
        service,
        access,
        repository,
        draft_repository,
        store,
        sources,
        versions,
        canonical,
        gateway,
        sources,
    )


def test_folder_creation_writes_revision_then_commits_one_skill_aggregate():
    service, access, repository, _drafts, store, _git, *_extra = _service()

    result = service.create_from_folder(
        space_id=7,
        actor_id="owner-1",
        request_id="create-1",
        files=_folder(),
    )

    assert result.skill_id == 51
    assert result.created is True
    access.require_space_member.assert_called_once_with(space_id=7, user_id="owner-1")
    call = repository.create_space_skill.call_args.kwargs
    assert call["skill_data"]["name"] == "draft-skill"
    assert call["skill_data"]["description"] is None
    assert call["skill_data"]["draft_description"] == "Draft description"
    assert call["skill_data"]["draft_target_version"] == 1
    assert call["skill_data"]["draft_status"] == "EDITING"
    assert call["skill_data"]["draft_source_kind"] == "FOLDER"
    assert call["skill_data"]["creation_request_id"] == "create-1"
    assert call["ownership_data"] == {
        "space_id": 7,
        "created_by": "owner-1",
        "env": "test",
    }
    assert call["owner_grant_data"]["user_id"] == "owner-1"
    ref = DraftRevisionRef.from_locator(
        tenant="tenant-a", env="test", locator=call["skill_data"]["zip_url"]
    )
    stored = store.read_revision(ref)
    assert stored.name == "draft-skill"
    assert stored.description == "Draft description"


def test_folder_creation_replays_original_without_writing_another_revision():
    service, _access, repository, _drafts, store, _git, *_extra = _service()
    store.write_revision = MagicMock(wraps=store.write_revision)
    first = service.create_from_folder(
        space_id=7,
        actor_id="owner-1",
        request_id="create-1",
        files=_folder(),
    )
    persisted = repository.create_space_skill.call_args.kwargs["skill_data"]
    repository.get_creation_by_request_id.return_value = {
        "skill_id": first.skill_id,
        "space_id": 7,
        "request_hash": persisted["creation_request_hash"],
    }

    replay = service.create_from_folder(
        space_id=7,
        actor_id="owner-1",
        request_id="create-1",
        files=_folder(),
    )

    assert replay.skill_id == first.skill_id
    assert replay.created is False
    assert store.write_revision.call_count == 1
    assert repository.create_space_skill.call_count == 1


def test_folder_creation_rejects_key_reuse_for_different_content_or_space():
    service, _access, repository, _drafts, _store, _git, *_extra = _service()
    repository.get_creation_by_request_id.return_value = {
        "skill_id": 51,
        "space_id": 8,
        "request_hash": "different",
    }

    with pytest.raises(SpaceSkillIdempotencyConflictError):
        service.create_from_folder(
            space_id=7,
            actor_id="owner-1",
            request_id="create-1",
            files=_folder(),
        )

    repository.create_space_skill.assert_not_called()


def test_folder_creation_cleans_new_revision_when_database_commit_fails():
    service, _access, repository, _drafts, store, _git, *_extra = _service()
    repository.create_space_skill.side_effect = RuntimeError("database failed")
    store.delete_revision = MagicMock(wraps=store.delete_revision)

    with pytest.raises(RuntimeError, match="database failed"):
        service.create_from_folder(
            space_id=7,
            actor_id="owner-1",
            request_id="create-1",
            files=_folder(),
        )

    store.delete_revision.assert_called_once()
    deleted_ref = store.delete_revision.call_args.args[0]
    with pytest.raises(DraftContentStoreError) as missing:
        store.read_revision(deleted_ref)
    assert missing.value.code is DraftContentStoreErrorCode.NOT_FOUND


def test_folder_creation_does_not_hide_cleanup_failure():
    service, _access, repository, _drafts, store, _git, *_extra = _service()
    repository.create_space_skill.side_effect = RuntimeError("database failed")
    store.delete_revision = MagicMock(
        side_effect=DraftContentStoreError(
            DraftContentStoreErrorCode.DELETE_FAILED, "cleanup failed"
        )
    )

    with pytest.raises(RuntimeError, match="database failed"):
        service.create_from_folder(
            space_id=7,
            actor_id="owner-1",
            request_id="create-1",
            files=_folder(),
        )

    store.delete_revision.assert_called_once()


def test_git_creation_uses_the_same_package_and_persistence_pipeline():
    service, _access, repository, _drafts, store, sources, *_extra = _service()
    sources.fetch_git_snapshot.return_value = GitSkillSnapshot(
        repo_url="https://example.com/team/skills.git",
        resolved_branch="main",
        commit_sha="a" * 40,
        source_subdir="skills/draft-skill",
        files=tuple(
            (path.removeprefix("draft-skill/"), content) for path, content in _folder()
        ),
    )

    result = service.create_from_git(
        space_id=7,
        actor_id="owner-1",
        request_id="git-create-1",
        git_url="https://example.com/team/skills.git",
        branch=None,
        subdir=None,
    )

    assert result.skill_id == 51
    sources.fetch_git_snapshot.assert_called_once_with(
        git_url="https://example.com/team/skills.git",
        branch=None,
        subdir=None,
    )
    skill_data = repository.create_space_skill.call_args.kwargs["skill_data"]
    assert skill_data["source_type"] == "GIT"
    assert skill_data["draft_source_kind"] == "GIT"
    assert skill_data["source_repo_url"] == "https://example.com/team/skills.git"
    assert skill_data["source_branch"] == "main"
    assert skill_data["source_commit_sha"] == "a" * 40
    assert skill_data["source_subdir"] == "skills/draft-skill"
    ref = DraftRevisionRef.from_locator(
        tenant="tenant-a", env="test", locator=skill_data["zip_url"]
    )
    assert store.read_revision(ref).name == "draft-skill"


def _seed_draft(store, *, source_kind="FOLDER"):
    package = SkillPackageValidator(SkillParser()).validate_directory(_folder())
    from agentclaw.community.core.skill_center.draft_content import (
        DraftRevisionIdentity,
    )

    ref = store.write_revision(
        DraftRevisionIdentity(
            tenant="tenant-a",
            env="test",
            skill_uuid="11111111-1111-4111-8111-111111111111",
            target_version=1,
            revision_id="22222222-2222-4222-8222-222222222222",
        ),
        package,
    )
    return {
        "skill_id": 51,
        "skill_uuid": ref.skill_uuid,
        "name": "draft-skill",
        "draft_description": "Draft description",
        "target_version": 1,
        "status": "EDITING",
        "locator": ref.locator,
        "source_kind": source_kind,
        "source_repo_url": (
            "https://example.com/team/skills.git" if source_kind == "GIT" else None
        ),
        "source_branch": "main" if source_kind == "GIT" else None,
        "source_subdir": "skills/draft-skill" if source_kind == "GIT" else None,
        "source_commit_sha": "a" * 40 if source_kind == "GIT" else None,
        "space_type": "PERSONAL",
    }


def test_draft_tree_and_utf8_file_read_use_the_current_revision():
    service, _access, _repository, drafts, store, _git, *_extra = _service()
    drafts.get_draft.return_value = _seed_draft(store)

    tree = service.get_draft_file_tree(space_id=7, skill_id=51, actor_id="owner-1")
    file = service.read_draft_file(
        space_id=7, skill_id=51, actor_id="owner-1", path="SKILL.md"
    )

    assert tree.revision_id == "22222222-2222-4222-8222-222222222222"
    assert [(item.path, item.size) for item in tree.files] == [
        ("SKILL.md", len(file.content.encode())),
        ("references/example.md", 7),
    ]
    assert file.path == "SKILL.md"
    assert "name: draft-skill" in file.content


def test_personal_draft_save_writes_new_revision_and_commits_revision_cas():
    service, _access, _repository, drafts, store, _git, *_extra = _service()
    current = _seed_draft(store)
    drafts.get_draft.return_value = current
    drafts.replace_draft_revision.return_value = current["locator"]
    store.delete_revision = MagicMock(wraps=store.delete_revision)

    result = service.save_draft_file(
        space_id=7,
        skill_id=51,
        actor_id="owner-1",
        path="references/example.md",
        content="updated",
        expected_revision_id="22222222-2222-4222-8222-222222222222",
        fencing_token=None,
    )

    assert result.revision_id != "22222222-2222-4222-8222-222222222222"
    call = drafts.replace_draft_revision.call_args.kwargs
    assert call["expected_revision_id"] == "22222222-2222-4222-8222-222222222222"
    assert call["fencing_token"] is None
    assert call["new_description"] == "Draft description"
    store.delete_revision.assert_called_once()


def test_draft_save_cleans_new_revision_when_cas_loses():
    service, _access, _repository, drafts, store, _git, *_extra = _service()
    drafts.get_draft.return_value = _seed_draft(store)
    drafts.replace_draft_revision.side_effect = DraftRevisionConflictError()
    store.delete_revision = MagicMock(wraps=store.delete_revision)

    with pytest.raises(DraftRevisionConflictError):
        service.save_draft_file(
            space_id=7,
            skill_id=51,
            actor_id="owner-1",
            path="references/example.md",
            content="updated",
            expected_revision_id="22222222-2222-4222-8222-222222222222",
            fencing_token=None,
        )

    assert store.delete_revision.call_count == 1
    assert store.delete_revision.call_args.args[0].revision_id != (
        "22222222-2222-4222-8222-222222222222"
    )


def test_git_refresh_failure_leaves_draft_and_store_untouched():
    service, _access, _repository, drafts, store, sources, *_extra = _service()
    drafts.get_draft.return_value = _seed_draft(store, source_kind="GIT")
    sources.fetch_git_snapshot.side_effect = RuntimeError("clone failed")
    store.write_revision = MagicMock(wraps=store.write_revision)

    with pytest.raises(RuntimeError, match="clone failed"):
        service.refresh_draft_from_git(
            space_id=7,
            skill_id=51,
            actor_id="owner-1",
            expected_revision_id="22222222-2222-4222-8222-222222222222",
            fencing_token=None,
        )

    assert store.write_revision.call_count == 0
    drafts.replace_draft_revision.assert_not_called()


def test_frozen_save_and_refresh_reject_before_external_io():
    service, _access, _repository, drafts, store, sources, *_extra = _service()
    frozen = _seed_draft(store, source_kind="GIT")
    frozen["status"] = "FROZEN"
    drafts.get_draft.return_value = frozen
    store.read_revision = MagicMock(wraps=store.read_revision)

    with pytest.raises(DraftFrozenError):
        service.save_draft_file(
            space_id=7,
            skill_id=51,
            actor_id="owner-1",
            path="SKILL.md",
            content="unchanged",
            expected_revision_id="old",
            fencing_token=1,
        )
    with pytest.raises(DraftFrozenError):
        service.refresh_draft_from_git(
            space_id=7,
            skill_id=51,
            actor_id="owner-1",
            expected_revision_id="old",
            fencing_token=1,
        )

    store.read_revision.assert_not_called()
    sources.fetch_git_snapshot.assert_not_called()


def test_upgrade_creates_one_vn_plus_one_draft_from_exact_published_store():
    (
        service,
        _access,
        _repository,
        drafts,
        store,
        _sources,
        versions,
        canonical,
        _gateway,
        _fetcher,
    ) = _service()
    drafts.get_upgrade_by_request_id.return_value = None
    versions.list_latest_published.return_value = (
        {
            "id": 91,
            "skill_id": 51,
            "version_ordinal": 1,
            "status": "PUBLISHED",
            "sc_version_number": "1.0.0",
            "sc_skill_id": None,
            "sc_version_id": None,
            "name": "draft-skill",
            "description": "Published description",
            "metadata_json": "{}",
            "published_at": None,
        },
    )
    canonical.read_version.return_value = CanonicalCenterVersion.from_files(
        CanonicalCenterVersionIdentity(
            skill_uuid="11111111-1111-4111-8111-111111111111",
            sc_version_number="1.0.0",
        ),
        {
            "SKILL.md": (
                b"---\nname: draft-skill\ndescription: Published description\n---\n"
            )
        },
    )
    drafts.get_skill_for_upgrade.return_value = {
        "skill_id": 51,
        "skill_uuid": "11111111-1111-4111-8111-111111111111",
        "name": "draft-skill",
        "space_type": "TEAM",
        "sc_team_id": 77,
    }
    drafts.create_upgrade_draft.return_value = {
        "created": True,
        "draft": {
            "skill_id": 51,
            "skill_uuid": "11111111-1111-4111-8111-111111111111",
            "name": "draft-skill",
            "draft_description": "Published description",
            "target_version": 2,
            "status": "EDITING",
            "locator": "unused",
            "source_kind": "PUBLISHED_VERSION",
            "source_repo_url": None,
            "source_branch": None,
            "source_subdir": None,
            "source_commit_sha": None,
            "space_type": "TEAM",
            "sc_team_id": 77,
        },
    }

    result = service.create_upgrade_draft(
        space_id=7, skill_id=51, actor_id="owner-1", request_id="upgrade-2"
    )

    assert result.target_version == 2
    assert result.source_kind == "PUBLISHED_VERSION"
    call = drafts.create_upgrade_draft.call_args.kwargs
    assert call["expected_version_id"] == 91
    assert call["target_version"] == 2
    ref = DraftRevisionRef.from_locator(
        tenant="tenant-a", env="test", locator=call["new_locator"]
    )
    assert store.read_revision(ref).description == "Published description"


def test_upgrade_repairs_missing_canonical_store_from_exact_sc_download():
    (
        service,
        _access,
        _repository,
        drafts,
        _store,
        _git,
        versions,
        canonical,
        gateway,
        sources,
    ) = _service()
    drafts.get_upgrade_by_request_id.return_value = None
    drafts.get_skill_for_upgrade.return_value = {
        "skill_id": 51,
        "skill_uuid": "11111111-1111-4111-8111-111111111111",
        "name": "draft-skill",
        "space_type": "TEAM",
        "sc_team_id": 77,
    }
    versions.list_latest_published.return_value = (
        {
            "id": 91,
            "skill_id": 51,
            "version_ordinal": 1,
            "status": "PUBLISHED",
            "sc_version_number": "1.0.0",
            "sc_skill_id": None,
            "sc_version_id": None,
            "name": "draft-skill",
            "description": "Published description",
            "metadata_json": "{}",
            "published_at": None,
        },
    )
    exact = CanonicalCenterVersion.from_files(
        CanonicalCenterVersionIdentity(
            skill_uuid="11111111-1111-4111-8111-111111111111",
            sc_version_number="1.0.0",
        ),
        {
            "SKILL.md": (
                b"---\nname: draft-skill\ndescription: Published description\n---\n"
            )
        },
    )
    canonical.read_version.side_effect = [
        CanonicalCenterStoreError(CanonicalCenterStoreErrorCode.NOT_READY, "missing"),
        exact,
    ]
    gateway.get_exact_download.return_value = SkillCenterExactDownload(
        skill_code="11111111-1111-4111-8111-111111111111",
        version_number="1.0.0",
        download_url="https://download.example/exact.zip",
        sha256="a" * 64,
    )
    package = SkillPackageValidator(SkillParser()).validate_directory(
        list(exact.file_map.items())
    )
    sources.fetch_exact_package.return_value = package.canonical_zip
    drafts.create_upgrade_draft.return_value = {
        "created": True,
        "draft": {
            "skill_id": 51,
            "skill_uuid": exact.identity.skill_uuid,
            "name": "draft-skill",
            "draft_description": "Published description",
            "target_version": 2,
            "status": "EDITING",
            "locator": (
                "draft://11111111-1111-4111-8111-111111111111/"
                "v2/22222222-2222-4222-8222-222222222222"
            ),
            "source_kind": "PUBLISHED_VERSION",
            "source_repo_url": None,
            "source_branch": None,
            "source_subdir": None,
            "source_commit_sha": None,
            "space_type": "TEAM",
            "sc_team_id": 77,
        },
    }

    service.create_upgrade_draft(
        space_id=7, skill_id=51, actor_id="owner-1", request_id="upgrade-2"
    )

    sources.fetch_exact_package.assert_called_once_with(
        url="https://download.example/exact.zip", expected_sha256="a" * 64
    )
    canonical.write_version.assert_called_once()
