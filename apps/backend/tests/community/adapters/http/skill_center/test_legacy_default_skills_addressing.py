"""Legacy admin operations keep their resource address after an Owner change."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.adapters.http.skill_center import skillsets_default_routes as routes
from agentclaw.community.adapters.http.skill_center.schemas import (
    SetDefaultSkillsFastRequest,
    SetDefaultSkillsRequest,
)


@pytest.fixture
def world(tmp_path, monkeypatch):
    bot = {
        "id": 7,
        "bot_id": "service",
        "entity_id": "original",
        "owner_id": "successor",
        "entity_type": "staff",
        "bot_type": "service",
        "active_engine": "openclaw",
    }
    bots = Mock()
    bots.list_matches_by_legacy_locator.side_effect = lambda bot_id, locator: (
        [bot] if bot_id == "service" and locator in {"original", "successor"} else []
    )
    paths = Mock()
    for kind in ("skills", "skills_local", "skills_repo", "engine"):
        getattr(paths, f"get_bot_{kind}_dir").return_value = tmp_path / kind
    sets = Mock()
    sets.ensure_default_skill_set.return_value = {"id": "10", "name": "Default"}
    sets.get_set_skills.return_value = [{"id": "1"}, {"id": "2"}]
    sets.skill_set_repo.get_skills_in_set.return_value = [{"id": "1"}]
    skills = Mock()
    skills.get_skills_in_path.return_value = []
    skills.get_skill.return_value = None
    skills.get_skill_by_link_name.return_value = None
    skills.get_skill_by_path.return_value = None
    skills.list_skills.return_value = []
    set_factory = Mock()
    set_factory.create.return_value = sets
    skill_factory = Mock()
    skill_factory.create.return_value = skills
    repo_dir = tmp_path / "repository"
    default_dir = tmp_path / "defaults"
    repo_dir.mkdir()
    default_dir.mkdir()
    from agentclaw.community.core.workspace import path_factory

    monkeypatch.setattr(path_factory, "get_global_skills_repo_dir", lambda: repo_dir)
    monkeypatch.setattr(path_factory, "get_global_skills_default_dir", lambda: default_dir)
    return SimpleNamespace(
        sets=sets, skills=skills, set_factory=set_factory, skill_factory=skill_factory,
        repo_dir=repo_dir, default_dir=default_dir, paths=paths,
        kwargs={
            "entity_id": None, "entity_type": None, "bot_id": "service",
            "engine_type": None,
            "ctx": SimpleNamespace(user_id="successor", bot_id="service"),
            "bot_repo": bots, "path_factory": paths,
            "skill_service_factory": skill_factory,
            "skill_set_service_factory": set_factory,
        },
    )


def assert_stable_scope(world):
    world.set_factory.create.assert_called_once_with(
        entity_id="original", bot_id="service", engine_type="openclaw", entity_type="staff"
    )
    skill_scope = world.skill_factory.create.call_args.kwargs
    assert (skill_scope["entity_id"], skill_scope["bot_id"]) == ("original", "service")
    for call in world.paths.mock_calls:
        assert call.args[:2] == ("original", "service")


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", [None, "original", "successor"])
async def test_init_sync_keeps_stable_bot_scope_and_existing_members(world, locator):
    """Login fallback, cached locator and new Owner must reach the same set."""
    world.kwargs["entity_id"] = locator
    world.skills.get_skills_in_path.return_value = [
        {"id": "already", "full_path": "/repo/already"},
        {"id": "new", "full_path": "/repo/new"},
        {"id": "incomplete"},
    ]
    world.skills.get_skill_by_path.side_effect = lambda path: (
        {"id": "1"} if path == "git://already" else None
    )
    world.sets._create_skill_from_market.return_value = {"id": "3"}
    response = await routes.init_and_sync_default_skills(**world.kwargs)
    assert response.model_dump() == {
        "success": True,
        "skill_set": {"id": "10", "name": "Default", "type": "default"},
        "sync": {"scanned": 3, "created": 1, "added": 1, "skipped": 1,
                 "failed": 0, "errors": []},
    }
    world.sets.skill_set_repo.add_skill_to_set.assert_called_once_with("10", "3")
    assert_stable_scope(world)


@pytest.mark.asyncio
@pytest.mark.parametrize("fast", [False, True])
async def test_replace_defaults_keeps_wire_and_stable_scope(world, fast):
    """Both old replacement endpoints handle ID, alias and market lookup."""
    world.skills.get_skill.side_effect = lambda skill_id, **kw: {"id": "1"}
    world.skills.get_skill_by_link_name.side_effect = lambda name: (
        {"id": "3"} if name == "alias" else None
    )
    world.skills.get_skills_in_path.return_value = [
        {"id": "catalog", "link_name": "market"}
    ]
    world.sets._create_skill_from_market.return_value = {"id": "4"}
    request_type = SetDefaultSkillsFastRequest if fast else SetDefaultSkillsRequest
    endpoint = routes.set_default_skills_fast if fast else routes.set_default_skills
    response = await endpoint(
        request_type(skill_ids=["1", "alias", "market", "missing"]), **world.kwargs
    )
    assert response.success is True
    assert response.skill_set == {"id": "10", "name": "Default", "type": "default"}
    assert response.sync == {
        "requested": 4, "added": 2, "removed": 1, "skipped": 1, "not_found": 1,
        "copied": 0, "deleted": 0, "errors": ["Skill not found: missing"],
    }
    world.sets.skill_set_repo.remove_skill_from_set.assert_called_once_with("10", "2")
    assert [call.args for call in world.sets.skill_set_repo.add_skill_to_set.call_args_list] == [
        ("10", "3"), ("10", "4")
    ]
    assert_stable_scope(world)


@pytest.mark.asyncio
async def test_replace_defaults_syncs_real_files_and_preserves_requested_skill(world):
    """The old copying endpoint must preserve content, replace stale directories."""
    source = world.repo_dir / "tools" / "kept"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("Current skill content")
    kept = world.default_dir / "kept"
    kept.mkdir()
    (kept / "old.txt").write_text("Stale content")
    stale = world.default_dir / "removed"
    stale.mkdir()
    world.skills.get_skill.return_value = {
        "id": "1", "name": "kept", "git_path": "git://tools/kept"
    }
    response = await routes.set_default_skills(
        SetDefaultSkillsRequest(skill_ids=["1"]), **world.kwargs
    )
    assert response.sync["copied"] == 1
    assert response.sync["deleted"] == 1
    assert response.sync["errors"] == []
    assert (kept / "SKILL.md").read_text() == "Current skill content"
    assert not (kept / "old.txt").exists()
    assert not stale.exists()
    assert_stable_scope(world)


@pytest.mark.asyncio
async def test_init_reports_partial_failure_without_losing_successful_members(world):
    world.skills.get_skills_in_path.return_value = [
        {"id": "bad", "full_path": "/repo/bad"},
        {"id": "good", "full_path": "/repo/good"},
    ]

    def lookup(path):
        if path == "git://bad":
            raise RuntimeError("catalog unavailable")
        return {"id": "3"}

    world.skills.get_skill_by_path.side_effect = lookup
    response = await routes.init_and_sync_default_skills(**world.kwargs)
    assert response.sync["failed"] == 1
    assert response.sync["added"] == 1
    assert response.sync["errors"] == ["Failed to process bad: catalog unavailable"]
    world.sets.skill_set_repo.add_skill_to_set.assert_called_once_with("10", "3")
    assert_stable_scope(world)


@pytest.mark.asyncio
async def test_unresolved_bot_preserves_detached_catalog_address(world):
    world.kwargs.update(entity_id="catalog-user", bot_id="detached")
    world.sets.get_set_skills.return_value = []
    response = await routes.set_default_skills_fast(
        SetDefaultSkillsFastRequest(skill_ids=[]), **world.kwargs
    )
    assert response.success is True
    assert response.sync["errors"] == []
    scope = world.set_factory.create.call_args.kwargs
    assert (scope["entity_id"], scope["bot_id"]) == ("catalog-user", "detached")
