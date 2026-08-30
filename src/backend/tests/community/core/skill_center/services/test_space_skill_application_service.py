"""Behavior tests for the Space Skill Draft application seam."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStoreError,
    DraftContentStoreErrorCode,
    DraftRevisionRef,
)
from agentclaw.community.core.skill_center.errors import (
    DraftRevisionConflictError,
    SpaceSkillIdempotencyConflictError,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.space_skill_application_service import (
    SpaceSkillApplicationService,
)
from agentclaw.community.core.skill_center.git_snapshot import GitSkillSnapshot
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.testing.draft_content_store import LocalDraftContentStore


def _folder(description: str = "Draft description") -> list[tuple[str, bytes]]:
    return [
        (
            "draft-skill/SKILL.md",
            (
                "---\n"
                "name: draft-skill\n"
                f"description: {description}\n"
                "---\n"
                "# Draft\n"
            ).encode(),
        ),
        ("draft-skill/references/example.md", b"example"),
    ]


def _service():
    access = MagicMock()
    access.require_space_member.return_value = (MagicMock(), MagicMock())
    repository = MagicMock()
    draft_repository = MagicMock()
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
    git_snapshots = MagicMock()
    service = SpaceSkillApplicationService(
        access=access,
        repository=repository,
        draft_repository=draft_repository,
        package_validator=validator,
        draft_store=store,
        git_snapshots=git_snapshots,
        env_provider=lambda: "test",
        tenant_provider=lambda: "tenant-a",
    )
    return service, access, repository, draft_repository, store, git_snapshots


def test_folder_creation_writes_revision_then_commits_one_skill_aggregate():
    service, access, repository, _drafts, store, _git = _service()

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
    service, _access, repository, _drafts, store, _git = _service()
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
    service, _access, repository, _drafts, _store, _git = _service()
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
    service, _access, repository, _drafts, store, _git = _service()
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
    service, _access, repository, _drafts, store, _git = _service()
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
    service, _access, repository, _drafts, store, git_snapshots = _service()
    git_snapshots.fetch.return_value = GitSkillSnapshot(
        repo_url="https://example.com/team/skills.git",
        resolved_branch="main",
        commit_sha="a" * 40,
        source_subdir="skills/draft-skill",
        files=tuple(
            (path.removeprefix("draft-skill/"), content)
            for path, content in _folder()
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
    git_snapshots.fetch.assert_called_once_with(
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
    from agentclaw.community.core.skill_center.draft_content import DraftRevisionIdentity

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
    service, _access, _repository, drafts, store, _git = _service()
    drafts.get_draft.return_value = _seed_draft(store)

    tree = service.get_draft_file_tree(
        space_id=7, skill_id=51, actor_id="owner-1"
    )
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
    service, _access, _repository, drafts, store, _git = _service()
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
    service, _access, _repository, drafts, store, _git = _service()
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
    service, _access, _repository, drafts, store, git_snapshots = _service()
    drafts.get_draft.return_value = _seed_draft(store, source_kind="GIT")
    git_snapshots.fetch.side_effect = RuntimeError("clone failed")
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
