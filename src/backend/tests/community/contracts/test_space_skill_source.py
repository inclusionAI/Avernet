"""Conformance tests for the external Space Skill source Plugin API."""

from __future__ import annotations

import pytest

from agentclaw.community.api.space_skill_application_service import (
    SpaceSkillApplicationServiceProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository,
)
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.space_skill_source import (
    GitSkillSnapshot,
    GitSnapshotError,
    SpaceSkillSourcePlugin,
)
from agentclaw.community.utils.env_utils import get_current_env


def _space_member(world) -> tuple[int, str]:
    actor_id = "space-skill-source-owner"
    env = get_current_env()
    repository = world.get(SpaceSkillRepository)
    space = repository.create_space(
        {
            "space_code": "source-contract-space",
            "space_type": "TEAM",
            "name": "Source Contract",
            "created_by": actor_id,
            "env": env,
        }
    )
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            SpaceMemberModel(
                space_id=space["id"],
                user_id=actor_id,
                role="ADMIN",
                status="ACTIVE",
                created_by=actor_id,
                env=env,
            )
        )
    return space["id"], actor_id


def test_git_creation_consumes_the_source_plugin(world) -> None:
    space_id, actor_id = _space_member(world)
    source = world.get(SpaceSkillSourcePlugin)
    source.set_response(
        "fetch_git_snapshot",
        GitSkillSnapshot(
            repo_url="https://example.com/repo.git",
            resolved_branch="main",
            commit_sha="a" * 40,
            source_subdir="",
            files=(
                (
                    "SKILL.md",
                    b"---\nname: source-contract\ndescription: contract\n---\n",
                ),
            ),
        ),
    )
    service = world.get(SpaceSkillApplicationServiceProtocol)

    outcome = service.create_from_git(
        space_id=space_id,
        actor_id=actor_id,
        request_id="source-contract-create",
        git_url="https://example.com/repo.git",
        branch=None,
        subdir=None,
    )

    assert outcome.created is True
    assert outcome.skill_id > 0
    calls = source.calls_to("fetch_git_snapshot")
    assert len(calls) == 1
    assert calls[0].kwargs["git_url"] == "https://example.com/repo.git"


def test_git_source_failure_propagates_without_persisting_a_skill(world) -> None:
    space_id, actor_id = _space_member(world)
    source = world.get(SpaceSkillSourcePlugin)
    source.set_override(
        "fetch_git_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(GitSnapshotError("clone failed")),
    )
    service = world.get(SpaceSkillApplicationServiceProtocol)

    with pytest.raises(GitSnapshotError):
        service.create_from_git(
            space_id=space_id,
            actor_id=actor_id,
            request_id="source-contract-failure",
            git_url="https://example.com/failure.git",
            branch=None,
            subdir=None,
        )
    assert len(source.calls_to("fetch_git_snapshot")) == 1
    assert world.get(SpaceSkillRepository).get_creation_by_request_id(
        request_id="source-contract-failure", env=get_current_env()
    ) is None
