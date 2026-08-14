"""tests/unit/skill_center/test_dependencies_skills.py

Smoke tests that the skill_center DI bindings resolve cleanly.
The legacy ``dependencies/skills.py`` module is gone; bindings now
live on the injector via :class:`SkillCenterModule` /
:class:`TestingSkillCenterModule`.
"""
import pytest

pytestmark = pytest.mark.unit


def test_skill_center_sync_service_resolves_from_injector(test_injector):
    """``SkillCenterSyncService`` should be resolvable via the injector."""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService

    svc = test_injector.get(SkillCenterSyncService)
    assert svc is not None
    assert hasattr(svc, "force_sync")
    assert hasattr(svc, "cleanup_stale")
    assert hasattr(svc, "is_synced")


def test_skill_propagation_service_resolves_from_injector(test_injector):
    """``SkillPropagationService`` should resolve and carry a log_repo."""
    from agentclaw.community.core.skill_center.services.skill_propagation_service import SkillPropagationService

    svc = test_injector.get(SkillPropagationService)
    assert svc is not None
    assert svc._log_repo is not None
