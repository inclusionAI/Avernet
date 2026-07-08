from agentclaw.community.core.models.skill_center_sync_log import SkillCenterSyncLog


def test_skill_center_sync_log_columns():
    log = SkillCenterSyncLog(
        skill_uuid="test-uuid",
        version="1.0.0",
        env="dev",
        status="pending",
    )
    assert log.skill_uuid == "test-uuid"
    assert log.version == "1.0.0"
    assert log.env == "dev"
    assert log.status == "pending"
    assert hasattr(log, "gmt_created")
    assert hasattr(log, "gmt_modified")


def test_tablename():
    assert SkillCenterSyncLog.__tablename__ == "ac_skill_center_sync_log"
