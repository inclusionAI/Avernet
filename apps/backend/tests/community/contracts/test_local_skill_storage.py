"""Local package locators and device writes share one resolved root."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.plugins.community.local_skill_storage import (
    EngineLocalSkillStorage,
)
from agentclaw.community.plugins.local_skill_storage import ConfiguredLocalSkillStorage


def factory(resolver, pool=None):
    bot = {
        "bot_id": "b",
        "entity_id": "u",
        "entity_type": "staff",
        "active_engine": "claude_code",
        "bot_type": "personal",
    }
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = bot
    repo.get_by_id.return_value = bot
    paths = MagicMock()
    paths.get_bot_skills_local_dir.return_value = Path("/host/skills/skills-local")
    return SkillServiceFactory(
        skill_repo=MagicMock(),
        bot_repo=repo,
        skill_repo_sync=MagicMock(),
        category_repo=MagicMock(),
        device_fs_dispatcher=MagicMock(),
        market_cache=MagicMock(),
        git_sync_service_factory=MagicMock(),
        path_factory=paths,
        pool_layout_paths=lambda *_: pool,
        local_skill_storage=resolver,
    )


def package(f):
    return f.local_skill_package_storage(
        entity_id="u",
        owner_id="u",
        bot_id="b",
        engine_type="claude_code",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        name="sample",
    )


@pytest.mark.parametrize(
    "resolver,expected",
    [
        (ConfiguredLocalSkillStorage(), "/host/skills/skills-local"),
        (
            EngineLocalSkillStorage(),
            "/home/admin/.claude_code/workspace/skills/skills-local",
        ),
    ],
)
def test_creation_and_reopen_use_same_address(resolver, expected):
    f = factory(resolver)
    locator, storage = package(f)
    assert locator == expected + "/sample"
    assert storage.directory == locator
    reopened = f.local_skill_package_storage_for_locator(
        entity_id="u",
        owner_id="u",
        bot_id="b",
        engine_type="claude_code",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        locator=locator,
        skill_name="sample",
    )
    assert reopened.directory == storage.directory
    service = f.create(
        entity_id="u",
        bot_owner_id="u",
        bot_id="b",
        engine_type="claude_code",
        local_dir=Path("/host/skills/skills-local"),
    )
    assert service.local_dir == Path(expected)
    assert service.runtime_uses_pool_paths is False


def test_pool_state_remains_authoritative():
    f = factory(
        EngineLocalSkillStorage(), pool=("/active", "/pool/local", "/pool/repo")
    )
    locator, storage = package(f)
    assert locator == "/pool/local/sample"
    assert storage.directory == locator
    assert f.create(entity_id="u", bot_id="b").runtime_uses_pool_paths is True


def test_historical_locator_is_not_redirected_to_new_root():
    f = factory(EngineLocalSkillStorage())
    with pytest.raises(ValueError, match="escapes skills-local"):
        f.local_skill_package_storage_for_locator(
            entity_id="u",
            owner_id="u",
            bot_id="b",
            engine_type="claude_code",
            entity_type="staff",
            is_desktop=False,
            is_teclaw=False,
            locator="/home/admin/.openclaw/workspace/skills/skills-local/sample",
            skill_name="sample",
        )
    f._device_fs_dispatcher.for_bot.assert_not_called()


@pytest.mark.parametrize(
    "recorded_root",
    [
        "/home/admin/.claude_code/workspace/skills/skills-local",
        "/home/admin/.aicoding/workspace/skills/skills-local",
    ],
)
def test_aicoding_legacy_engine_view_alias_reopens_current_package(recorded_root):
    f = factory(ConfiguredLocalSkillStorage())
    bot = {
        "bot_id": "b",
        "entity_id": "u",
        "entity_type": "staff",
        "active_engine": "claude_code",
        "template_type": "aicoding",
        "bot_type": "personal",
    }
    f._bot_repo.get_by_id_and_owner.return_value = bot
    f._bot_repo.get_by_id.return_value = bot
    f._path_factory.get_bot_skills_local_dir.side_effect = (
        lambda _entity_id, _bot_id, engine, *_args, **_kwargs: Path(
            f"/aidesktop/u/b/{engine}/workspace/skills/skills-local"
        )
    )

    current_locator, current = f.local_skill_package_storage(
        entity_id="u",
        owner_id="u",
        bot_id="b",
        engine_type="claude_code",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        name="sample",
    )
    reopened = f.local_skill_package_storage_for_locator(
        entity_id="u",
        owner_id="u",
        bot_id="b",
        engine_type="claude_code",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        locator=f"{recorded_root}/sample",
        skill_name="sample",
    )

    assert current_locator == (
        "/aidesktop/u/b/aicoding/workspace/skills/skills-local/sample"
    )
    assert reopened.directory == current.directory


def test_aicoding_legacy_host_alias_reopens_current_package():
    f = factory(ConfiguredLocalSkillStorage())
    bot = {
        "bot_id": "b",
        "entity_id": "u",
        "entity_type": "staff",
        "active_engine": "aicoding",
        "bot_type": "personal",
    }
    f._bot_repo.get_by_id_and_owner.return_value = bot
    f._bot_repo.get_by_id.return_value = bot
    f._path_factory.get_bot_skills_local_dir.side_effect = (
        lambda _entity_id, _bot_id, engine, *_args, **_kwargs: Path(
            f"/aidesktop/u/b/{engine}/workspace/skills/skills-local"
        )
    )

    _, current = f.local_skill_package_storage(
        entity_id="u",
        owner_id="u",
        bot_id="b",
        engine_type="aicoding",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        name="sample",
    )
    reopened = f.local_skill_package_storage_for_locator(
        entity_id="u",
        owner_id="u",
        bot_id="b",
        engine_type="aicoding",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        locator=("/aidesktop/u/b/claude_code/workspace/skills/skills-local/sample"),
        skill_name="sample",
    )

    assert reopened.directory == current.directory


@pytest.mark.parametrize(
    ("locator", "skill_name"),
    [
        (
            "/aidesktop/u/other/claude_code/workspace/skills/skills-local/sample",
            "sample",
        ),
        (
            "/aidesktop/u/b/claude_code/workspace/skills/skills-local/sample",
            "other-skill",
        ),
        (
            "/aidesktop/u/b/hermes/workspace/skills/skills-local/sample",
            "sample",
        ),
        (
            "/aidesktop/u/b/aicoding/workspace/skills/skills-local/"
            "temporary/../sample",
            "sample",
        ),
        (
            "/aidesktop/u/b/aicoding/workspace/skills/skills-local/..",
            "..",
        ),
    ],
)
def test_aicoding_legacy_aliases_remain_exactly_bot_and_skill_scoped(
    locator, skill_name
):
    f = factory(ConfiguredLocalSkillStorage())
    bot = {
        "bot_id": "b",
        "entity_id": "u",
        "entity_type": "staff",
        "active_engine": "aicoding",
        "bot_type": "personal",
    }
    f._bot_repo.get_by_id_and_owner.return_value = bot
    f._bot_repo.get_by_id.return_value = bot
    f._path_factory.get_bot_skills_local_dir.side_effect = (
        lambda _entity_id, bot_id, engine, *_args, **_kwargs: Path(
            f"/aidesktop/u/{bot_id}/{engine}/workspace/skills/skills-local"
        )
    )

    with pytest.raises(ValueError, match="escapes skills-local"):
        f.local_skill_package_storage_for_locator(
            entity_id="u",
            owner_id="u",
            bot_id="b",
            engine_type="aicoding",
            entity_type="staff",
            is_desktop=False,
            is_teclaw=False,
            locator=locator,
            skill_name=skill_name,
        )


def test_aicoding_pool_rejects_a_legacy_claude_code_alias():
    f = factory(
        ConfiguredLocalSkillStorage(),
        pool=(
            "/home/admin/.claude/skills",
            "/home/admin/.aicoding/workspace/skills-pool/skills-local",
            "/home/admin/.aicoding/workspace/skills-pool/skills-repo",
        ),
    )
    bot = {
        "bot_id": "b",
        "entity_id": "u",
        "entity_type": "staff",
        "active_engine": "aicoding",
        "bot_type": "personal",
    }
    f._bot_repo.get_by_id_and_owner.return_value = bot
    f._bot_repo.get_by_id.return_value = bot

    with pytest.raises(ValueError, match="escapes skills-local"):
        f.local_skill_package_storage_for_locator(
            entity_id="u",
            owner_id="u",
            bot_id="b",
            engine_type="aicoding",
            entity_type="staff",
            is_desktop=False,
            is_teclaw=False,
            locator=("/home/admin/.claude_code/workspace/skills/skills-local/sample"),
            skill_name="sample",
        )


def test_teclaw_does_not_inherit_aicoding_legacy_locator_aliases():
    f = factory(ConfiguredLocalSkillStorage())
    bot = {
        "bot_id": "b",
        "entity_id": "u",
        "entity_type": "staff",
        "active_engine": "aicoding",
        "bot_type": "personal",
    }
    f._bot_repo.get_by_id_and_owner.return_value = bot
    f._bot_repo.get_by_id.return_value = bot
    f._path_factory.get_bot_skills_local_dir.return_value = Path("skills-local")

    with pytest.raises(ValueError, match="escapes skills-local"):
        f.local_skill_package_storage_for_locator(
            entity_id="u",
            owner_id="u",
            bot_id="b",
            engine_type="aicoding",
            entity_type="staff",
            is_desktop=False,
            is_teclaw=True,
            locator=("/home/admin/.claude_code/workspace/skills/skills-local/sample"),
            skill_name="sample",
        )


def test_other_runtime_roots_are_unchanged():
    resolver = EngineLocalSkillStorage()
    for runtime in ("openclaw", "aicoding", "hermes", "teclaw"):
        assert resolver.resolve_root(runtime, Path("/configured/local")) == Path(
            "/configured/local"
        )


def test_composition_selects_addresses_without_changing_public_path_factory():
    from injector import Injector
    from agentclaw.community.di.modules.skill_center_module import SkillCenterModule
    from agentclaw.community.di.modules.infrastructure.community.skill_center import (
        CommunitySkillCenterClientModule,
    )
    from agentclaw.community.plugin_api.local_skill_storage import (
        LocalSkillStorageResolver,
    )

    base = Injector([SkillCenterModule()])
    community = Injector([SkillCenterModule(), CommunitySkillCenterClientModule()])
    assert isinstance(base.get(LocalSkillStorageResolver), ConfiguredLocalSkillStorage)
    assert isinstance(community.get(LocalSkillStorageResolver), EngineLocalSkillStorage)


def test_explicit_pool_owned_request_keeps_its_selected_root():
    f = factory(EngineLocalSkillStorage())
    service = f.create(
        entity_id="u",
        bot_id="b",
        engine_type="claude_code",
        local_dir=Path("/selected/pool/local"),
        runtime_uses_pool_paths=True,
    )
    assert service.local_dir == Path("/selected/pool/local")
    assert service.runtime_uses_pool_paths is True


@pytest.mark.parametrize("world_name", ["world", "community_world"])
def test_real_factory_consumes_profile_binding_for_create_and_rejection(
    request, world_name, monkeypatch, tmp_path
):
    from agentclaw.community.plugin_api.local_skill_storage import (
        LocalSkillStorageResolver,
    )

    current_world = request.getfixturevalue(world_name)
    consumer = current_world.get(SkillServiceFactory)
    resolver = current_world.get(LocalSkillStorageResolver)
    assert consumer._local_skill_storage is resolver
    configured = tmp_path / "configured/skills-local"
    bot = {
        "bot_id": "b",
        "entity_id": "u",
        "entity_type": "staff",
        "active_engine": "claude_code",
        "bot_type": "personal",
    }
    monkeypatch.setattr(consumer, "_pool_layout_paths", lambda *_: None)
    monkeypatch.setattr(consumer._bot_repo, "get_by_id_and_owner", lambda *_: bot)
    monkeypatch.setattr(consumer._bot_repo, "get_by_id", lambda *_: bot)
    monkeypatch.setattr(
        consumer._path_factory, "get_bot_skills_local_dir", lambda *_, **__: configured
    )
    device_factory = MagicMock()
    monkeypatch.setattr(consumer._device_fs_dispatcher, "for_bot", device_factory)
    resolve = MagicMock(wraps=resolver.resolve_root)
    monkeypatch.setattr(resolver, "resolve_root", resolve)
    expected = (
        Path("/home/admin/.claude_code/workspace/skills/skills-local")
        if world_name == "community_world"
        else configured
    )

    locator, storage = package(consumer)
    assert locator == str(expected / "sample")
    assert storage.directory == locator
    resolve.assert_any_call("claude_code", configured)
    resolve.reset_mock()
    device_factory.reset_mock()
    with pytest.raises(ValueError, match="escapes skills-local"):
        consumer.local_skill_package_storage_for_locator(
            entity_id="u",
            owner_id="u",
            bot_id="b",
            engine_type="claude_code",
            entity_type="staff",
            is_desktop=False,
            is_teclaw=False,
            locator=str(tmp_path / "outside/sample"),
            skill_name="sample",
        )
    resolve.assert_any_call("claude_code", configured)
    device_factory.assert_not_called()
