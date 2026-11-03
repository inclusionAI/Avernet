"""Real graph: delivery pauses never turn off recording or alter AUTO outcomes."""

import pytest
from injector import Module

from agentclaw.community.core.config.sofa import sofa_config
from agentclaw.community.core.repository.protocols.spaces import (
    SpaceRepositoryProtocol,
    SpaceManagementRepositoryProtocol,
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository,
)
from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderEventStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.core.work_orders.work_order_service_protocol import (
    WorkOrderServiceProtocol,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink
from tests.community.core.space_knowledge.test_membership_real_database import (
    database as database,
)


class Sink:
    def __init__(self):
        self.enabled = True
        self.calls = []

    def is_enabled(self):
        return self.enabled

    def apply_snapshot(self, snapshot):
        assert self.enabled
        self.calls.append(snapshot)


def enabled_graph(database, sink):
    class Storage(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=database)
            binder.bind(SpaceMembershipSink, to=sink)

    return build_injector(profile=DeployProfile.TEST, extra_modules=[Storage()])


@pytest.fixture
def recording(monkeypatch, database):
    monkeypatch.setenv("SERVER_ENV", "pre")
    monkeypatch.setitem(
        vars(sofa_config), "user_config", {"space_knowledge_capture": {"enabled": True}}
    )
    sink = Sink()
    graph = enabled_graph(database, sink)
    spaces = graph.get(SpaceRepositoryProtocol)
    with spaces.create_team_transaction(
        name="Team", creator_id="owner", creator_user_name=None, env="pre"
    ) as created:
        sid = created.id
    return graph, database, sink, sid


def test_paused_sink_preserves_ordered_changes_and_retired_identity_after_restart(
    recording,
):
    graph, db, sink, sid = recording
    sync = graph.get(SpaceMembershipSynchronizer)
    sync.ensure_synced(space_id=sid, environment="pre")
    spaces = graph.get(SpaceRepositoryProtocol)
    management = graph.get(SpaceManagementRepositoryProtocol)
    sink.enabled = False
    spaces.add_member(
        space_id=sid,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    spaces.update_member_role(
        space_id=sid, user_id="alice", role=SpaceRole.ADMIN, env="pre"
    )
    spaces.delete_member(space_id=sid, user_id="alice", env="pre")
    management.rename_team(space_id=sid, name="Renamed", actor_id="owner", env="pre")
    management.delete_team(space_id=sid, with_members=True, env="pre")
    assert [item.revision for item in sink.calls] == [1]
    assert graph.get(SpaceKnowledgeSyncRepositoryProtocol).progress(
        space_id=sid, environment="pre", app="agentclaw"
    ) == (6, 1, "disabled")
    created = graph.get(SpaceSkillRepository).create_space(
        {
            "space_code": "replacement",
            "space_type": "TEAM",
            "name": "Replacement",
            "created_by": "owner",
            "env": "pre",
        }
    )
    assert created["id"] > sid
    sink.enabled = True
    restarted = enabled_graph(db, sink).get(SpaceMembershipSynchronizer)
    while restarted.deliver_next(space_id=sid, environment="pre"):
        pass
    assert [item.revision for item in sink.calls] == [1, 2, 3, 4, 5, 6]
    assert sink.calls[2].body["members"][0] == {"staff_no": "alice", "role": "ADMIN"}
    assert "alice" not in {m["staff_no"] for m in sink.calls[3].body["members"]}
    assert sink.calls[-1].body["status"] == "disabled"
    assert sink.calls[-1].body["members"] == []


def test_auto_service_reports_failure_after_atomic_capacity_rollback(recording):
    graph, db, _sink, sid = recording
    with db.orm_session() as session:
        session.add_all(
            [
                SpaceMemberModel(
                    space_id=sid,
                    user_id=f"member-{i}",
                    role="MEMBER",
                    created_by="owner",
                    env="pre",
                )
                for i in range(1999)
            ]
        )
    graph.get(SpaceMembershipSynchronizer).ensure_synced(
        space_id=sid, environment="pre"
    )
    result = graph.get(WorkOrderServiceProtocol).create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type="SPACE_JOIN",
        biz_id=str(sid),
        event_type="SPACE_JOIN_APPLIED",
        applicant_user_id="overflow",
        approver_user_ids=[],
        recipient_user_ids=["overflow"],
        title="Join",
        content=None,
        apply_reason=None,
        biz_data=None,
        actor_id="overflow",
    )
    assert result.status is WorkOrderEventStatus.FAILED
    with db.orm_session() as session:
        assert session.get(WorkOrderModel, result.work_order_id).status == "FAILED"
        assert (
            session.query(SpaceMemberModel)
            .filter_by(space_id=sid, user_id="overflow")
            .count()
            == 0
        )
        assert session.query(Snapshot).count() == 1
        notice = session.query(WorkOrderNotificationModel).one()
        assert notice.recipient_user_id == "overflow"
        assert result.notification_ids == [notice.id]
