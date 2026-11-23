"""Single-row audit storage participates in the caller's transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def snapshot(**changes):
    from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditSnapshot
    values = dict(
        change_id="change-1", source_bot_pk=10, bot_id="bot-1", entity_id="entity-1",
        env="pre", tenant_id="teamclaw", creator_id="creator", operator_id="operator",
        bot_created_at=datetime(2026, 1, 1), change_created_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
        submitted_at=datetime(2026, 11, 22, tzinfo=timezone.utc), before_stage="verify",
        desired_stage="online", before_status="validating", publish_id=20, version=3,
        finished_at=None, finish_status="pending", final_publish_status=None, ext_json="{}",
    )
    values.update(changes)
    return ReleaseAuditSnapshot(**values)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    ReleaseAuditModel.__table__.create(engine)
    factory = sessionmaker(engine)
    @contextmanager
    def session():
        with factory.begin() as value:
            yield value
    yield session
    engine.dispose()


@pytest.mark.parametrize("local", [False, True])
def test_create_then_complete_updates_one_row_and_keeps_first_completion(session_factory, local):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    from agentclaw.community.plugins.local.service_bot_release_audit import LocalDatabaseServiceBotReleaseAudit
    plugin = (LocalDatabaseServiceBotReleaseAudit if local else DatabaseServiceBotReleaseAudit)()
    finished = datetime(2026, 11, 22, 1, tzinfo=timezone.utc)
    with session_factory() as session:
        plugin.create(session, snapshot())
    with session_factory() as session:
        assert plugin.complete(session, change_id="change-1", publish_id=20, env="pre",
                               expected_stage="online", finish_status="succeeded",
                               final_publish_status="success", finished_at=finished)
    with session_factory() as session:
        assert not plugin.complete(session, change_id="change-1", publish_id=20, env="pre",
                                   expected_stage="online", finish_status="failed",
                                   final_publish_status="failed", finished_at=datetime.now(timezone.utc))
        row = session.query(ReleaseAuditModel).one()
        assert row.finished_at == finished.replace(tzinfo=None)
        assert row.finish_status == "succeeded"
        assert row.creator_id == "creator"
        assert row.operator_id == "operator"
        assert row.desired_stage == "online" and row.env == "pre"
        assert row.version == 3


def test_audit_create_is_rolled_back_with_callers_transaction(session_factory):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    with pytest.raises(RuntimeError, match="abort"):
        with session_factory() as session:
            DatabaseServiceBotReleaseAudit().create(session, snapshot())
            raise RuntimeError("abort")
    with session_factory() as session:
        assert session.query(ReleaseAuditModel).count() == 0


def test_expected_stage_refuses_premature_completion(session_factory):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    plugin = DatabaseServiceBotReleaseAudit()
    with session_factory() as session:
        plugin.create(session, snapshot())
        assert not plugin.complete(session, change_id="change-1", publish_id=20, env="pre",
                                   expected_stage="verify", finish_status="succeeded",
                                   final_publish_status="validating", finished_at=datetime.now(timezone.utc))
        session.expire_all()
        assert session.query(ReleaseAuditModel).one().finish_status == "pending"


def test_sink_uses_current_tenant_scope(session_factory):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    plugin = DatabaseServiceBotReleaseAudit()
    with avernet_tenant_scope("tenant-a"):
        with session_factory() as session:
            plugin.create(session, snapshot(tenant_id="tenant-a"))
    with avernet_tenant_scope("tenant-b"):
        with session_factory() as session:
            assert session.query(ReleaseAuditModel).count() == 0
            with pytest.raises(ValueError, match="tenant"):
                plugin.create(session, snapshot(tenant_id="tenant-a"))
            with pytest.raises(ValueError, match="missing"):
                plugin.complete(session, change_id="change-1", publish_id=20, env="pre",
                                expected_stage=None, finish_status="failed",
                                final_publish_status="failed", finished_at=datetime.now(timezone.utc))


def test_ext_rejects_credentials():
    with pytest.raises(ValueError, match="extension"):
        snapshot(ext_json='{"token":"do-not-store"}')


def test_schema_has_only_single_audit_table():
    from agentclaw.community.plugin_api.models import Base
    assert "ac_service_bot_release_audit_event" not in Base.metadata.tables
    assert "revision" not in ReleaseAuditModel.__table__.columns
    assert "snapshot_json" not in ReleaseAuditModel.__table__.columns


def test_audit_logs_success_failure_and_completion_without_credentials(session_factory, caplog):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    plugin = DatabaseServiceBotReleaseAudit()
    with caplog.at_level("INFO"):
        with session_factory() as session:
            plugin.create(session, snapshot())
            plugin.complete(session, change_id="change-1", publish_id=20, env="pre",
                            expected_stage="online", finish_status="succeeded",
                            final_publish_status="success", finished_at=datetime.now(timezone.utc))
            with pytest.raises(ValueError, match="missing"):
                plugin.complete(session, change_id="absent", publish_id=20, env="pre",
                                expected_stage=None, finish_status="failed",
                                final_publish_status="failed", finished_at=datetime.now(timezone.utc))
        with session_factory() as session:
            with pytest.raises(ValueError, match="tenant"):
                plugin.create(session, snapshot(tenant_id="other-tenant"))
    for event in ("create.request", "create.success", "create.failed", "complete.request", "complete.success", "complete.failed"):
        assert f"release_audit.{event}" in caplog.text
    assert "version=3" in caplog.text and "final_publish_status=success" in caplog.text
    with pytest.raises(ValueError):
        snapshot(ext_json='{"Authorization":"never-log-this-credential"}')
    assert "never-log-this-credential" not in caplog.text
