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
    SkillSetActivator,
    SkillSetActivatorFactory,
    SkillSetService,
    SkillSetSwitcher,
    SkillSetSwitcherFactory,
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
    assert svc._local_skill_path_adapter(
        f"{engine_root}/skills/skills-local/handmade"
    ) == f"{engine_root}/skills-pool/skills-local/handmade"
    assert svc._local_skill_locator_adapter(
        f"{engine_root}/skills/skills-local/handmade"
    ) == f"{engine_root}/skills-pool/skills-local/handmade"


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

    assert str(svc.skill_service.local_dir).endswith(
        "/skills-pool/skills-local"
    )
    assert str(svc.skill_service.repo_dir).endswith(
        "/skills-pool/skills-repo"
    )
    assert str(svc.local_dir).endswith("/skills-pool/skills-local")
    assert str(svc.repo_dir).endswith("/skills-pool/skills-repo")
    assert svc.skill_service._local_skill_path_adapter(
        "/home/admin/.openclaw/workspace/skills/skills-local/handmade"
    ).endswith("/skills-pool/skills-local/handmade")


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
    assert str(svc.skill_service.local_dir).endswith(
        "/skills-pool/skills-local"
    )


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
    assert str(svc.local_dir) == (
        "/home/admin/.openclaw/workspace/skills/skills-local"
    )
    assert str(svc.repo_dir) == (
        "/home/admin/.openclaw/workspace/skills/skills-repo"
    )


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
                "local:///home/admin/.openclaw/workspace/skills/"
                "skills-local/handmade"
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
            "/home/admin/.openclaw/workspace/skills-pool/"
            "skills-local/handmade",
            "/home/admin/.openclaw/workspace/skills/handmade",
        ),
        (
            "/home/admin/.openclaw/workspace/skills-pool/"
            "skills-repo/business/reviewer",
            "/home/admin/.openclaw/workspace/skills/reviewer",
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
