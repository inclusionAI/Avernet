"""Tests for skill_center feature_flags module."""
import os
from agentclaw.community.core.skill_center.feature_flags import SkillCenterFlags


class TestSkillCenterFlags:
    def test_defaults_all_false_except_write_skill_uuid(self):
        flags = SkillCenterFlags(
            nas_sync_enabled=False,
            center_uri_enabled=False,
            propagation_enabled=False,
            symlink_verify_auto_fix=False,
            write_skill_uuid=True,
        )
        assert flags.write_skill_uuid is True
        assert flags.nas_sync_enabled is False

    def test_from_env_reads_env_vars(self):
        os.environ["SC_NAS_SYNC_ENABLED"] = "true"
        os.environ["SC_PROPAGATION_ENABLED"] = "true"
        try:
            flags = SkillCenterFlags.from_env()
            assert flags.nas_sync_enabled is True
            assert flags.propagation_enabled is True
            assert flags.center_uri_enabled is False
        finally:
            del os.environ["SC_NAS_SYNC_ENABLED"]
            del os.environ["SC_PROPAGATION_ENABLED"]

    def test_any_blocking_enabled_true_when_any_flag_on(self):
        flags = SkillCenterFlags(
            nas_sync_enabled=False,
            center_uri_enabled=True,
            propagation_enabled=False,
            symlink_verify_auto_fix=False,
            write_skill_uuid=True,
        )
        assert flags.any_blocking_enabled() is True

    def test_any_blocking_enabled_false_when_all_off(self):
        flags = SkillCenterFlags(
            nas_sync_enabled=False,
            center_uri_enabled=False,
            propagation_enabled=False,
            symlink_verify_auto_fix=False,
            write_skill_uuid=True,
        )
        assert flags.any_blocking_enabled() is False
