"""Publication audit delivery consumes the output Plugin through real DI and DB."""
from datetime import datetime, timezone

import pytest
from injector import InstanceProvider

from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditEvent, ReleaseAuditSnapshot
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditEventModel, ReleaseAuditModel
from agentclaw.community.plugin_api.service_bot_release_audit import ServiceBotReleaseAuditPlugin
from agentclaw.community.utils.env_utils import get_current_env


def _event():
    return ReleaseAuditEvent(snapshot=ReleaseAuditSnapshot(
        change_id="contract-change", source_bot_pk=10, bot_id="bot-contract", entity_id="entity-contract",
        env=get_current_env(), tenant_id="teamclaw", creator_id="creator", operator_id="editor",
        bot_created_at=None, change_created_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        submitted_at=datetime(2026, 11, 13, tzinfo=timezone.utc), before_stage="draft",
        desired_stage="verify", before_status="draft", publish_id=20, version=1,
        finished_at=datetime(2026, 11, 13, 0, 1, tzinfo=timezone.utc),
        finish_status="succeeded", final_publish_status="validating", revision=2,
    ))


def _seed(world):
    event = _event()
    with world.get(DatabasePlugin).orm_session() as db:
        db.add(ReleaseAuditEventModel(
            event_id=event.event_id, change_id=event.change_id, revision=event.revision,
            publish_id=event.snapshot.publish_id, env=event.snapshot.env,
            avernet_tenant=event.snapshot.tenant_id, payload_json=event.to_json(),
        ))
    return event


def test_delivery_routes_complete_snapshot_through_selected_plugin(world):
    from agentclaw.community.core.service_bot.services.release_audit_delivery import ReleaseAuditDelivery
    selected = world.get(ServiceBotReleaseAuditPlugin)
    calls = []

    class RecordingSink:
        def record(self, event):
            calls.append(event)
            selected.record(event)

    world.injector.binder.bind(ServiceBotReleaseAuditPlugin, to=InstanceProvider(RecordingSink()))
    event = _seed(world)
    delivery = world.get(ReleaseAuditDelivery)
    assert delivery.deliver(event.event_id) is True
    assert calls == [event]
    with world.get(DatabasePlugin).orm_session() as db:
        row = db.query(ReleaseAuditModel).one()
        assert row.finish_status == "succeeded"
        assert row.final_publish_status == "validating"
        assert row.operator_id == "editor"
        assert row.creator_id == "creator"
        assert db.query(ReleaseAuditEventModel).one().delivered_at is not None
    assert delivery.deliver(event.event_id) is True
    assert len(calls) == 1
    assert delivery.deliver("missing:2") is False


def test_failed_plugin_does_not_ack_event_and_can_be_retried(world):
    from agentclaw.community.core.service_bot.services.release_audit_delivery import ReleaseAuditDelivery
    selected = world.get(ServiceBotReleaseAuditPlugin)
    calls = []

    class RecoveringSink:
        def record(self, event):
            calls.append(event)
            if len(calls) == 1:
                raise RuntimeError("sink temporarily unavailable")
            selected.record(event)

    world.injector.binder.bind(ServiceBotReleaseAuditPlugin, to=InstanceProvider(RecoveringSink()))
    event = _seed(world)
    delivery = world.get(ReleaseAuditDelivery)
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        delivery.deliver(event.event_id)
    with world.get(DatabasePlugin).orm_session() as db:
        assert db.query(ReleaseAuditEventModel).one().delivered_at is None
        assert db.query(ReleaseAuditModel).count() == 0
    assert delivery.deliver(event.event_id) is True
    assert calls == [event, event]
    with world.get(DatabasePlugin).orm_session() as db:
        assert db.query(ReleaseAuditModel).one().finished_at == event.snapshot.finished_at.replace(tzinfo=None)
        assert db.query(ReleaseAuditEventModel).one().delivered_at is not None
