"""Verify db parameter has been removed from service constructors."""
import inspect


class TestNoDbParameter:
    """Service layer should not accept deprecated db parameter."""

    def test_skill_service_no_db(self):
        from agentclaw.community.core.skill_center.services.skill_service import SkillService
        sig = inspect.signature(SkillService.__init__)
        assert "db" not in sig.parameters

    def test_skill_set_service_no_db(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        sig = inspect.signature(SkillSetService.__init__)
        assert "db" not in sig.parameters

    def test_skill_set_switcher_no_db(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetSwitcher
        sig = inspect.signature(SkillSetSwitcher.__init__)
        assert "db" not in sig.parameters

    def test_skill_set_activator_no_db(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetActivator
        sig = inspect.signature(SkillSetActivator.__init__)
        assert "db" not in sig.parameters

    def test_metadata_writer_no_db(self):
        from agentclaw.community.core.skill_center.utils.skill_metadata_writer import SkillSetMetadataWriter
        sig = inspect.signature(SkillSetMetadataWriter.__init__)
        assert "db" not in sig.parameters
