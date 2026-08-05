"""Regression guard for the skill-center service factories.

Same blind spot that hid the ResourceServiceFactory prod break:
binding-resolution only *constructs* the factory; route/service tests
mock it. So a broken ``create()`` body (wrong kwarg, signature drift
against the service ctor) ships to prod uncaught.

These resolve the REAL factories from the test injector and invoke
``create()`` the way the routers do — covering every skill-center
factory ``create()`` body (both branches where they differ).
"""

from pathlib import Path

import pytest

from agentclaw.community.core.skill_center.factories import (
    SkillParameterServiceFactory,
    SkillServiceFactory,
    SkillSetServiceFactory,
)
from agentclaw.community.core.skill_center.services.skill_parameter_service import (
    SkillParameterService,
)
from agentclaw.community.core.skill_center.services.skill_service import SkillService
from agentclaw.community.core.skill_center.services.skill_set_service import (
    ActivateResult,
    SkillSetActivator,
    SkillSetActivatorFactory,
    SkillSetService,
    SkillSetSwitcher,
    SkillSetSwitcherFactory,
    SwitchResult,
)


def test_real_skill_service_factory_create(test_injector):
    factory = test_injector.get(SkillServiceFactory)
    svc = factory.create()
    assert isinstance(svc, SkillService)
    assert svc.runtime_uses_pool_paths is False


@pytest.mark.parametrize(
    ("engine", "engine_root", "active_dir"),
    [
        (
            "openclaw",
            "/home/admin/.openclaw/workspace",
            "/home/admin/.openclaw/workspace/skills",
        ),
        (
            "claude_code",
            "/home/admin/.claude_code/workspace",
            "/home/admin/.claude/skills",
        ),
        (
            "aicoding",
            "/home/admin/.aicoding/workspace",
            "/home/admin/.claude/skills",
        ),
        (
            "hermes",
            "/home/admin/.hermes/workspace",
            "/home/admin/.hermes/skills",
        ),
    ],
)
def test_pool_active_factory_scopes_direct_skill_crud_to_canonical_pool(
    test_injector,
    engine,
    engine_root,
    active_dir,
):
    factory = test_injector.get(SkillServiceFactory)
    factory._pool_layout_paths = lambda *_: (
        active_dir,
        f"{engine_root}/skills-pool/skills-local",
        f"{engine_root}/skills-pool/skills-repo",
    )

    svc = factory.create(
        active_dir="/legacy/skills",
        local_dir="/legacy/skills/skills-local",
        repo_dir="/legacy/skills/skills-repo",
        entity_id="staff-1",
        bot_id="bot-1",
        engine_type=engine,
    )

    assert svc.active_dir == Path(active_dir)
    assert svc.local_dir == Path(f"{engine_root}/skills-pool/skills-local")
    assert svc.repo_dir == Path(f"{engine_root}/skills-pool/skills-repo")
    assert svc.runtime_uses_pool_paths is True
    assert (
        svc._local_skill_path_adapter(f"{engine_root}/skills/skills-local/handmade")
        == f"{engine_root}/skills-pool/skills-local/handmade"
    )
    assert (
        svc._local_skill_locator_adapter(f"{engine_root}/skills/skills-local/handmade")
        == f"{engine_root}/skills-pool/skills-local/handmade"
    )


def test_skill_factory_uses_bot_owner_for_pool_and_device_resolution(
    test_injector,
):
    factory = test_injector.get(SkillServiceFactory)
    pool_resolution_calls = []

    def resolve_pool_paths(owner_id, bot_id, engine_type):
        pool_resolution_calls.append((owner_id, bot_id, engine_type))
        return (
            "/home/admin/.hermes/skills",
            "/home/admin/.hermes/workspace/skills-pool/skills-local",
            "/home/admin/.hermes/workspace/skills-pool/skills-repo",
        )

    factory._pool_layout_paths = resolve_pool_paths
    service = factory.create(
        entity_id="project-42",
        bot_owner_id="owner-7",
        bot_id="bot-1",
        engine_type="hermes",
    )

    assert pool_resolution_calls == [("owner-7", "bot-1", "hermes")]
    assert service._device_owner_id == "owner-7"


def test_local_skill_package_storage_splits_path_entity_from_device_owner(
    test_injector,
):
    factory = test_injector.get(SkillServiceFactory)
    factory._pool_layout_paths = lambda *_: None
    path_calls = []
    device_calls = []

    class _Paths:
        def get_bot_skills_local_dir(
            self,
            entity_id,
            bot_id,
            engine_type,
            entity_type,
            *,
            is_desktop,
            is_teclaw,
        ):
            path_calls.append(
                (
                    entity_id,
                    bot_id,
                    engine_type,
                    entity_type,
                    is_desktop,
                    is_teclaw,
                )
            )
            return Path(f"/bots/{entity_id}/{bot_id}/skills-local")

    class _DeviceDispatcher:
        @staticmethod
        def for_bot(bot_id, owner_id):
            device_calls.append((bot_id, owner_id))
            return object()

    factory._path_factory = _Paths()
    factory._device_fs_dispatcher = _DeviceDispatcher()

    directory, _storage = factory.local_skill_package_storage(
        entity_id="project-42",
        owner_id="owner-7",
        bot_id="bot-1",
        engine_type="hermes",
        entity_type="project",
        is_desktop=False,
        is_teclaw=False,
        name="reviewer",
    )

    assert directory == "/bots/project-42/bot-1/skills-local/reviewer"
    assert path_calls == [("project-42", "bot-1", "hermes", "project", False, False)]
    assert device_calls == [("bot-1", "owner-7")]


def test_teclaw_legacy_local_skill_package_storage_uses_workspace_adapter(
    test_injector,
):
    factory = test_injector.get(SkillServiceFactory)
    factory._pool_layout_paths = lambda *_: None

    class _DeviceDispatcher:
        @staticmethod
        def for_bot(_bot_id, _owner_id):
            return object()

    factory._device_fs_dispatcher = _DeviceDispatcher()
    factory._path_factory.get_bot_skills_local_dir = (
        lambda _entity_id, _bot_id, *_args, **_kwargs: Path("skills-local")
    )

    directory, storage = factory.local_skill_package_storage(
        entity_id="project-42",
        owner_id="owner-7",
        bot_id="bot-1",
        engine_type="openclaw",
        entity_type="proj",
        is_desktop=False,
        is_teclaw=True,
        name="reviewer",
    )

    assert directory == "skills-local/reviewer"
    assert storage._device_directory == "workspace/skills-local/reviewer"


def test_legacy_relative_locator_cleanup_resolves_against_bot_local_dir(
    test_injector,
):
    factory = test_injector.get(SkillServiceFactory)
    factory._pool_layout_paths = lambda *_: None
    factory._device_fs_dispatcher.for_bot = lambda *_: object()
    factory._path_factory.get_bot_skills_local_dir = (
        lambda entity_id, bot_id, *_args, **_kwargs: Path(
            f"/bots/{entity_id}/{bot_id}/skills-local"
        )
    )

    storage = factory.local_skill_package_storage_for_locator(
        entity_id="project-42",
        owner_id="owner-7",
        bot_id="bot-1",
        engine_type="openclaw",
        entity_type="proj",
        is_desktop=False,
        is_teclaw=False,
        locator="reviewer",
    )

    assert storage._device_directory == (
        "/bots/project-42/bot-1/skills-local/reviewer"
    )


@pytest.mark.parametrize("locator", ["../skills-repo", "skills-local/.."])
def test_legacy_locator_cleanup_rejects_paths_outside_bot_local_dir(
    test_injector, locator
):
    factory = test_injector.get(SkillServiceFactory)
    factory._pool_layout_paths = lambda *_: None
    factory._device_fs_dispatcher.for_bot = lambda *_: object()
    factory._path_factory.get_bot_skills_local_dir = (
        lambda entity_id, bot_id, *_args, **_kwargs: Path(
            f"/bots/{entity_id}/{bot_id}/skills-local"
        )
    )

    with pytest.raises(ValueError, match="escapes skills-local"):
        factory.local_skill_package_storage_for_locator(
            entity_id="project-42",
            owner_id="owner-7",
            bot_id="bot-1",
            engine_type="openclaw",
            entity_type="proj",
            is_desktop=False,
            is_teclaw=False,
            locator=locator,
        )


def test_teclaw_legacy_locator_cleanup_reapplies_workspace_adapter(test_injector):
    factory = test_injector.get(SkillServiceFactory)
    factory._pool_layout_paths = lambda *_: None
    factory._device_fs_dispatcher.for_bot = lambda *_: object()
    factory._path_factory.get_bot_skills_local_dir = (
        lambda _entity_id, _bot_id, *_args, **_kwargs: Path("skills-local")
    )

    storage = factory.local_skill_package_storage_for_locator(
        entity_id="project-42",
        owner_id="owner-7",
        bot_id="bot-1",
        engine_type="openclaw",
        entity_type="proj",
        is_desktop=False,
        is_teclaw=True,
        locator="skills-local/reviewer",
    )

    assert storage._device_directory == "workspace/skills-local/reviewer"


def test_legacy_local_skill_packages_are_isolated_for_same_name_across_bots(
    test_injector,
):
    factory = test_injector.get(SkillServiceFactory)
    factory._pool_layout_paths = lambda *_: None

    class _DeviceDispatcher:
        @staticmethod
        def for_bot(_bot_id, _owner_id):
            return object()

    factory._device_fs_dispatcher = _DeviceDispatcher()
    factory._path_factory.get_bot_skills_local_dir = (
        lambda entity_id, bot_id, *_args, **_kwargs: Path(
            f"/bots/{entity_id}/{bot_id}/skills-local"
        )
    )

    directories = [
        factory.local_skill_package_storage(
            entity_id=f"entity-{index}",
            owner_id="owner",
            bot_id=f"bot-{index}",
            engine_type="openclaw",
            entity_type="staff",
            is_desktop=False,
            is_teclaw=False,
            name="same-name",
        )[0]
        for index in (1, 2)
    ]

    assert directories == [
        "/bots/entity-1/bot-1/skills-local/same-name",
        "/bots/entity-2/bot-2/skills-local/same-name",
    ]


def test_real_skill_set_service_factory_create_default_branch(test_injector):
    """No user_id/entity_id → the SKILLS_DIR-defaults (else) branch."""
    factory = test_injector.get(SkillSetServiceFactory)
    svc = factory.create()
    assert isinstance(svc, SkillSetService)


def test_real_skill_set_service_factory_create_bot_paths_branch(test_injector):
    """user_id/entity_id present → the _get_bot_paths (if) branch the
    routers actually take (skills.py passes user_id/entity_id/bot_id)."""
    factory = test_injector.get(SkillSetServiceFactory)
    factory._pool_layout_paths = lambda *_: None
    svc = factory.create(user_id="u1", entity_id="e1", bot_id="b1")
    assert isinstance(svc, SkillSetService)


def test_pool_active_factory_scopes_skill_writes_to_canonical_pool(test_injector):
    factory = test_injector.get(SkillSetServiceFactory)
    factory._pool_layout_paths = lambda *_: (
        "/home/admin/.openclaw/workspace/skills",
        "/home/admin/.openclaw/workspace/skills-pool/skills-local",
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
    )

    svc = factory.create(
        user_id="u1",
        entity_id="e1",
        bot_id="b1",
        engine_type="openclaw",
    )

    assert str(svc.skill_service.local_dir).endswith("/skills-pool/skills-local")
    assert str(svc.skill_service.repo_dir).endswith("/skills-pool/skills-repo")
    assert str(svc.local_dir).endswith("/skills-pool/skills-local")
    assert str(svc.repo_dir).endswith("/skills-pool/skills-repo")
    assert svc.skill_service._local_skill_path_adapter(
        "/home/admin/.openclaw/workspace/skills/skills-local/handmade"
    ).endswith("/skills-pool/skills-local/handmade")


def test_skill_set_factory_uses_owner_for_pool_lookup_and_entity_for_paths(
    test_injector,
):
    factory = test_injector.get(SkillSetServiceFactory)
    pool_resolution_calls = []

    def resolve_pool_paths(owner_id, bot_id, engine_type):
        pool_resolution_calls.append((owner_id, bot_id, engine_type))
        return (
            "/pool/active",
            "/pool/local",
            "/pool/repo",
        )

    factory._pool_layout_paths = resolve_pool_paths

    svc = factory.create(
        user_id="owner-7",
        entity_id="project-42",
        bot_id="bot-1",
        engine_type="hermes",
        entity_type="proj",
    )

    assert pool_resolution_calls == [("owner-7", "bot-1", "hermes")] * 2
    assert svc.user_id == "owner-7"
    assert svc.entity_id == "project-42"
    assert str(svc.local_dir) == "/pool/local"
    assert str(svc.skill_service.local_dir) == "/pool/local"


def test_desktop_pool_active_factory_uses_the_same_canonical_paths(
    test_injector,
):
    factory = test_injector.get(SkillSetServiceFactory)
    factory._bot_repo.get_by_id_and_owner = lambda *_: {
        "bot_type": "desktop",
    }
    factory._pool_layout_paths = lambda *_: (
        "/home/admin/.openclaw/workspace/skills",
        "/home/admin/.openclaw/workspace/skills-pool/skills-local",
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
    )

    svc = factory.create(
        user_id="u1",
        entity_id="e1",
        bot_id="b1",
        engine_type="openclaw",
    )

    assert svc.is_desktop is True
    assert str(svc.local_dir).endswith("/skills-pool/skills-local")
    assert str(svc.repo_dir).endswith("/skills-pool/skills-repo")
    assert str(svc.skill_service.local_dir).endswith("/skills-pool/skills-local")


def test_desktop_legacy_factory_preserves_existing_paths(test_injector):
    factory = test_injector.get(SkillSetServiceFactory)
    factory._bot_repo.get_by_id_and_owner = lambda *_: {
        "bot_type": "desktop",
    }
    factory._pool_layout_paths = lambda *_: None

    svc = factory.create(
        user_id="u1",
        entity_id="e1",
        bot_id="b1",
        engine_type="openclaw",
    )

    assert svc.is_desktop is True
    assert str(svc.local_dir) == ("/home/admin/.openclaw/workspace/skills/skills-local")
    assert str(svc.repo_dir) == ("/home/admin/.openclaw/workspace/skills/skills-repo")
    assert svc.skill_service.local_dir == svc.local_dir
    assert svc.skill_service.repo_dir == svc.repo_dir
    assert svc.skill_service.active_dir == svc.skills_dir


def test_desktop_pool_mapping_uses_canonical_pool_sources(test_injector):
    factory = test_injector.get(SkillSetServiceFactory)
    factory._bot_repo.get_by_id_and_owner = lambda *_: {
        "bot_type": "desktop",
    }
    factory._pool_layout_paths = lambda *_: (
        "/home/admin/.openclaw/workspace/skills",
        "/home/admin/.openclaw/workspace/skills-pool/skills-local",
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
    )
    svc = factory.create(
        user_id="u1",
        entity_id="e1",
        bot_id="b1",
        engine_type="openclaw",
    )
    svc.get_active_skills = lambda **_: [
        {
            "name": "handmade",
            "git_path": (
                "local:///home/admin/.openclaw/workspace/skills/skills-local/handmade"
            ),
        },
        {
            "name": "reviewer",
            "git_path": "git://business/reviewer",
        },
    ]

    mappings = svc.get_symlink_mappings(user_id="u1", bolt_id="b1")

    assert [(item.source, item.target) for item in mappings] == [
        (
            "/home/admin/.openclaw/workspace/skills-pool/skills-local/handmade",
            "/home/admin/.openclaw/workspace/skills/handmade",
        ),
        (
            "/home/admin/.openclaw/workspace/skills-pool/skills-repo/business/reviewer",
            "/home/admin/.openclaw/workspace/skills/reviewer",
        ),
    ]


def test_local_replacement_mapping_keeps_stable_skill_link_name(test_injector):
    factory = test_injector.get(SkillSetServiceFactory)
    factory._bot_repo.get_by_id_and_owner = lambda *_: {"bot_type": "desktop"}
    factory._pool_layout_paths = lambda *_: (
        "/home/admin/.openclaw/workspace/skills",
        "/home/admin/.openclaw/workspace/skills-pool/skills-local",
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
    )
    svc = factory.create(
        user_id="u1",
        entity_id="e1",
        bot_id="b1",
        engine_type="openclaw",
    )
    svc.get_active_skills = lambda **_: [
        {
            "name": "handmade",
            "git_path": (
                "local:///home/admin/.openclaw/workspace/skills/skills-local/"
                ".handmade.replacement-123"
            ),
        },
    ]

    mappings = svc.get_symlink_mappings(user_id="u1", bolt_id="b1")

    assert [(item.source, item.target) for item in mappings] == [
        (
            "/home/admin/.openclaw/workspace/skills-pool/skills-local/"
            ".handmade.replacement-123",
            "/home/admin/.openclaw/workspace/skills/handmade",
        ),
    ]


def test_relative_local_replacement_mapping_keeps_stable_skill_link_name(
    test_injector,
    monkeypatch,
):
    monkeypatch.setenv("DEPLOY_PROFILE", "production")
    factory = test_injector.get(SkillSetServiceFactory)
    factory._bot_repo.get_by_id_and_owner = lambda *_: {"bot_type": "teclaw"}
    factory._pool_layout_paths = lambda *_: (
        "/home/admin/.openclaw/workspace/skills",
        "/home/admin/.openclaw/workspace/skills-pool/skills-local",
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
    )
    svc = factory.create(
        user_id="u1",
        entity_id="e1",
        bot_id="b1",
        engine_type="openclaw",
    )
    svc.get_active_skills = lambda **_: [
        {
            "name": "handmade",
            "git_path": "local://skills-local/.handmade.replacement-123",
        },
    ]

    mappings = svc.get_symlink_mappings(user_id="u1", bolt_id="b1")

    assert [(item.source, item.target) for item in mappings] == [
        (
            "/home/admin/.openclaw/workspace/skills-pool/skills-local/"
            ".handmade.replacement-123",
            "/home/admin/.openclaw/workspace/skills/handmade",
        ),
    ]


@pytest.mark.asyncio
async def test_real_skill_parameter_service_factory_create(test_injector):
    """Async factory: builds the per-bot device_fs and constructs the
    SkillParameterService. load_on_init=False keeps it to the construction
    path under test.

    Factory now goes through ``DeviceContextResolver`` instead of the
    deprecated ``device_fs_dispatcher.for_bot``. The resolver hits the
    binding DB; tests don't seed binding rows, so we monkeypatch the
    factory's resolver provider to return a stub that produces a synthetic
    ctx, exercising the ``create()`` body all the way to the dispatcher.
    """
    from unittest.mock import MagicMock

    from agentclaw.community.core.devices.services.device_context import DeviceContext

    factory = test_injector.get(SkillParameterServiceFactory)

    fake_ctx = DeviceContext(
        provider="local",
        conn_info={},
        binding_id=0,
        bot_id="b1",
        user_id="u1",
    )
    fake_resolver = MagicMock()
    fake_resolver.resolve_for_bot.return_value = fake_ctx
    factory._resolver = fake_resolver

    svc = await factory.create(bot_id="b1", user_id="u1", load_on_init=False)
    assert isinstance(svc, SkillParameterService)


def test_real_skill_set_switcher_factory_create(test_injector):
    factory = test_injector.get(SkillSetSwitcherFactory)
    assert isinstance(factory.create(), SkillSetSwitcher)


def test_real_skill_set_activator_factory_create(test_injector):
    factory = test_injector.get(SkillSetActivatorFactory)
    assert isinstance(factory.create(), SkillSetActivator)


@pytest.mark.asyncio
async def test_bot_skill_set_activation_holds_the_layout_edit_lease(test_injector):
    from unittest.mock import AsyncMock

    activator = test_injector.get(SkillSetActivatorFactory).create()
    activator.skill_set_service.user_id = "owner"
    activator.skill_set_service.bot_id = "bot"

    class _Guard:
        def __init__(self):
            self.events = []

        async def acquire_for_edit_wait(self, *, scope):
            self.events.append(("acquire", scope))
            return "lease"

        def release(self, lease):
            self.events.append(("release", lease))

    guard = _Guard()
    activator._edit_guard = guard
    activator.skill_set_service._bot_repo.get_by_id_and_owner = lambda *_: {
        "env": "dev", "entity_id": "owner",
    }
    unlocked = AsyncMock(return_value=ActivateResult(success=True, message="ok"))
    activator._activate_skill_set_unlocked = unlocked

    result = await activator.activate_skill_set("7", user_id="owner")

    assert result.success is True
    assert guard.events[0][0] == "acquire"
    assert guard.events[0][1].env == "dev"
    assert guard.events[0][1].entity_id == "owner"
    assert guard.events[0][1].bot_id == "bot"
    assert guard.events[1] == ("release", "lease")
    unlocked.assert_awaited_once_with("7", user_id="owner", proxy_token=None)


@pytest.mark.asyncio
async def test_bot_skill_set_activation_uses_entity_id_for_the_layout_edit_lease(
    test_injector,
):
    from unittest.mock import AsyncMock

    activator = test_injector.get(SkillSetActivatorFactory).create()
    activator.skill_set_service.user_id = None
    activator.skill_set_service.entity_id = "owner"
    activator.skill_set_service.bot_id = "bot"

    class _Guard:
        def __init__(self):
            self.events = []

        async def acquire_for_edit_wait(self, *, scope):
            self.events.append(("acquire", scope))
            return "lease"

        def release(self, lease):
            self.events.append(("release", lease))

    guard = _Guard()
    activator._edit_guard = guard
    activator.skill_set_service._bot_repo.get_by_id_and_owner = lambda *_: {
        "env": "dev", "entity_id": "owner",
    }
    unlocked = AsyncMock(return_value=ActivateResult(success=True, message="ok"))
    activator._activate_skill_set_unlocked = unlocked

    result = await activator.activate_skill_set("7")

    assert result.success is True
    assert guard.events[0][0] == "acquire"
    assert guard.events[0][1].env == "dev"
    assert guard.events[0][1].entity_id == "owner"
    assert guard.events[0][1].bot_id == "bot"
    assert guard.events[1] == ("release", "lease")
    unlocked.assert_awaited_once_with("7", user_id=None, proxy_token=None)


@pytest.mark.asyncio
async def test_bot_skill_set_switch_and_sync_hold_the_layout_edit_lease(test_injector):
    from unittest.mock import AsyncMock

    switcher = test_injector.get(SkillSetSwitcherFactory).create()
    switcher.skill_set_service.user_id = "owner"
    switcher.skill_set_service.bot_id = "bot"

    class _Guard:
        def __init__(self):
            self.events = []

        async def acquire_for_edit_wait(self, *, scope):
            self.events.append(("acquire", scope))
            return f"lease-{len(self.events)}"

        def release(self, lease):
            self.events.append(("release", lease))

    guard = _Guard()
    switcher._edit_guard = guard
    switcher.skill_set_service._bot_repo.get_by_id_and_owner = lambda *_: {
        "env": "dev", "entity_id": "owner",
    }
    switch_unlocked = AsyncMock(
        return_value=SwitchResult(success=True, message="switched")
    )
    sync_unlocked = AsyncMock(return_value=SwitchResult(success=True, message="synced"))
    switcher._switch_to_skill_set_unlocked = switch_unlocked
    switcher._sync_skill_set_to_active_unlocked = sync_unlocked

    assert (await switcher.switch_to_skill_set("7", user_id="owner")).success
    assert (await switcher.sync_skill_set_to_active("8", user_id="owner")).success

    assert [event[0] for event in guard.events] == [
        "acquire", "release", "acquire", "release"
    ]
    switch_unlocked.assert_awaited_once_with("7", user_id="owner", proxy_token=None)
    sync_unlocked.assert_awaited_once_with("8", "owner")


def test_pool_paths_propagate_to_switcher_and_activator(test_injector):
    pool_paths = (
        "/home/admin/.openclaw/workspace/skills",
        "/home/admin/.openclaw/workspace/skills-pool/skills-local",
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
    )
    skill_set_factory = test_injector.get(SkillSetServiceFactory)
    skill_set_factory._pool_layout_paths = lambda *_: pool_paths

    switcher = test_injector.get(SkillSetSwitcherFactory).create(
        entity_id="staff_1",
        bot_id="bot-1",
        engine_type="openclaw",
    )
    activator = test_injector.get(SkillSetActivatorFactory).create(
        entity_id="staff_1",
        bot_id="bot-1",
        engine_type="openclaw",
    )

    assert str(switcher.local_dir).endswith("/skills-pool/skills-local")
    assert str(switcher.repo_dir).endswith("/skills-pool/skills-repo")
    assert str(activator.local_dir).endswith("/skills-pool/skills-local")
    assert str(activator.repo_dir).endswith("/skills-pool/skills-repo")
