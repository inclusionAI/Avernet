"""Database audit projection preserves publication identity and terminal facts."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def snapshot(**changes):
    from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditSnapshot

    values = dict(
        change_id="change-1", source_bot_pk=10, bot_id="service-1", entity_id="entity-1",
        env="pre", tenant_id="teamclaw", creator_id="creator", operator_id="operator",
        bot_created_at=datetime(2026, 1, 1), change_created_at=datetime(2026, 11, 13),
        submitted_at=datetime(2026, 11, 13), before_stage="verify", desired_stage="online",
        before_status="validating", publish_id=20, version=3, finished_at=None,
        finish_status="pending", final_publish_status=None, revision=1, ext_json="{}",
    )
    values.update(changes)
    return ReleaseAuditSnapshot(**values)


def event_for(value):
    from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditEvent
    return ReleaseAuditEvent(snapshot=value)


@pytest.fixture
def database(tmp_path):
    from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    ReleaseAuditModel.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    yield SimpleNamespace(orm_session=session, engine=engine)
    engine.dispose()


def test_event_round_trip_has_utc_times_and_immutable_ext():
    value = snapshot(submitted_at=datetime(2026, 11, 13, 8, tzinfo=timezone(timedelta(hours=8))))
    event = event_for(value)
    assert event.event_id == "change-1:1"
    assert event.revision == 1
    assert event.change_id == "change-1"
    assert value.to_dict()["submitted_at"] == "2026-11-13T00:00:00+00:00"
    assert type(event).from_json(event.to_json()) == event
    assert value.to_dict()["ext"] == {}


@pytest.mark.parametrize("cls_name", ["DatabaseServiceBotReleaseAudit", "LocalDatabaseServiceBotReleaseAudit"])
def test_sink_advances_once_and_old_events_do_not_undo_terminal(database, cls_name):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    from agentclaw.community.plugins.local.service_bot_release_audit import LocalDatabaseServiceBotReleaseAudit
    from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
    sink = {c.__name__: c for c in (DatabaseServiceBotReleaseAudit, LocalDatabaseServiceBotReleaseAudit)}[cls_name](database)
    start = snapshot()
    done = replace(start, revision=2, finished_at=datetime(2026, 11, 13, 0, 1),
                   finish_status="succeeded", final_publish_status="success")
    sink.record(event_for(start))
    sink.record(event_for(done))
    sink.record(event_for(done))
    sink.record(event_for(start))
    with database.orm_session() as db:
        row = db.query(ReleaseAuditModel).one()
        assert row.revision == 2
        assert row.finished_at == done.finished_at.replace(tzinfo=None)
        assert row.creator_id == "creator"
        assert row.operator_id == "operator"
        assert row.finish_status == "succeeded"
        assert row.env == "pre"
        assert row.desired_stage == "online"
        assert row.version == 3


def test_terminal_can_arrive_before_submission(database):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
    sink = DatabaseServiceBotReleaseAudit(database)
    done = snapshot(revision=2, finished_at=datetime(2026, 11, 13, 1),
                    finish_status="failed", final_publish_status="failed", creator_id=None)
    sink.record(event_for(done))
    sink.record(event_for(snapshot()))
    with database.orm_session() as db:
        row = db.query(ReleaseAuditModel).one()
        assert row.creator_id is None
        assert row.submitted_at == done.submitted_at.replace(tzinfo=None)
        assert row.finish_status == "failed"
        assert row.finished_at == done.finished_at.replace(tzinfo=None)


def test_same_revision_different_content_is_a_conflict(database, caplog):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    sink = DatabaseServiceBotReleaseAudit(database)
    sink.record(event_for(snapshot()))
    with pytest.raises(ValueError, match="conflict"):
        sink.record(event_for(snapshot(operator_id="another")))
    assert "release_audit.delivery.conflict" in caplog.text


def test_sink_rejects_cross_tenant_and_guard_filters_read(database):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
    sink = DatabaseServiceBotReleaseAudit(database)
    with avernet_tenant_scope("tenant-a"):
        sink.record(event_for(snapshot(tenant_id="tenant-a")))
    with avernet_tenant_scope("tenant-b"):
        with database.orm_session() as db:
            assert db.query(ReleaseAuditModel).count() == 0
        with pytest.raises(ValueError, match="tenant"):
            sink.record(event_for(snapshot(tenant_id="tenant-a")))
        sink.record(event_for(snapshot(tenant_id="tenant-b")))
        with database.orm_session() as db:
            assert db.query(ReleaseAuditModel).count() == 1


def test_sink_logs_request_success_failure_without_ext_secrets(database, caplog):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    sink = DatabaseServiceBotReleaseAudit(database)
    with caplog.at_level("INFO"):
        sink.record(event_for(snapshot(ext_json='{"error_message":"private-auth-material"}')))
        from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
        ReleaseAuditModel.__table__.drop(database.engine)
        with pytest.raises(Exception):
            sink.record(event_for(snapshot(change_id="other", ext_json='{"error_message":"private-auth-material"}')))
    assert "release_audit.delivery.request" in caplog.text
    assert "release_audit.delivery.success" in caplog.text
    assert "release_audit.delivery.failed" in caplog.text
    assert "private-auth-material" not in caplog.text


def test_ext_rejects_credentials_and_unknown_fields():
    with pytest.raises(ValueError, match="extension"):
        snapshot(ext_json='{"token":"do-not-store"}')
    with pytest.raises(ValueError, match="extension"):
        snapshot(ext_json='{"request_id":{"token":"do-not-store"}}')


@pytest.mark.parametrize("changes, expected", [
    ({"operator_id": ""}, "identity"),
    ({"desired_stage": "unknown"}, "desired stage"),
    ({"before_stage": "unknown"}, "before stage"),
    ({"revision": 3}, "revision"),
    ({"finish_status": "unknown"}, "finish status"),
    ({"revision": 2}, "revision must match"),
    ({"finish_status": "succeeded"}, "revision must match"),
    ({"revision": 2, "finish_status": "failed"}, "finished_at"),
    ({"finished_at": datetime(2026, 11, 13)}, "finished_at"),
    ({"ext_json": '[]'}, "extension"),
    ({"ext_json": '{"operation_ids":["wrong"]}'}, "operation_ids"),
])
def test_invalid_snapshot_is_rejected(changes, expected):
    with pytest.raises(ValueError, match=expected):
        snapshot(**changes)


def test_extension_is_canonical_json_and_full_event_rejects_wrong_identity():
    import json
    value = snapshot(ext_json='{"request_id":"request-1", "operation_ids": [12, 13], "schema_version":1}')
    assert value.to_dict()["ext"] == {
        "request_id": "request-1", "operation_ids": [12, 13], "schema_version": 1,
    }
    payload = json.loads(event_for(value).to_json())
    payload["event_id"] = "another:1"
    with pytest.raises(ValueError, match="identity mismatch"):
        type(event_for(value)).from_json(json.dumps(payload))


def test_concurrent_repeated_insert_leaves_one_row(database):
    from concurrent.futures import ThreadPoolExecutor
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
    sink = DatabaseServiceBotReleaseAudit(database)
    event = event_for(snapshot())
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: sink.record(event), range(16)))
    with database.orm_session() as db:
        assert db.query(ReleaseAuditModel).count() == 1
        assert db.query(ReleaseAuditModel).one().revision == 1


def test_source_bot_creation_timestamp_keeps_unknown_timezone():
    value = snapshot(bot_created_at=datetime(2026, 1, 1, 8))
    assert value.bot_created_at.tzinfo is None
    assert value.to_dict()["bot_created_at"] == "2026-01-01T08:00:00"
    assert event_for(value).snapshot.change_created_at.tzinfo == timezone.utc


def test_sink_logs_terminal_result_fields(database, caplog):
    from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit
    value = snapshot(revision=2, finished_at=datetime(2026, 11, 13, 0, 1),
                     finish_status="succeeded", final_publish_status="success")
    caplog.set_level("INFO")
    DatabaseServiceBotReleaseAudit(database).record(event_for(value))
    for field in ("version=3", "before_stage=verify", "before_status=validating",
                  "final_publish_status=success", "finished_at=2026-11-13T00:01:00+00:00"):
        assert field in caplog.text
