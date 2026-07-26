"""Regression guard for the skill-center service factories.

Same blind spot that hid the ResourceServiceFactory prod break:
binding-resolution only *constructs* the factory; route/service tests
mock it. So a broken ``create()`` body (wrong kwarg, signature drift
against the service ctor) ships to prod uncaught.

These resolve the REAL factories from the test injector and invoke
``create()`` the way the routers do — covering every skill-center
factory ``create()`` body (both branches where they differ).
"""
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
