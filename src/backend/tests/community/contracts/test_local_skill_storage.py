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
        )
    f._device_fs_dispatcher.for_bot.assert_not_called()


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
