"""L4 领域对象纯构造单测(Task 3)。

覆盖 ``TaskStatus`` 状态词汇表、``ContextSpec`` / ``CurrentSnapshot`` /
``ProgressSnapshot`` 的 ``to_dict`` / ``from_dict`` round-trip 与校验拒绝、
六个薄值对象(participants / activity / decision / message / artifact /
query)的构造与序列化,以及 ``EventRecord`` 与 ACCS ``EventEnvelope``
(spec §3.4.1)的双向适配。不触碰数据库,纯内存值对象。
"""

import importlib
import pkgutil
from datetime import datetime

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab import l4_context_primitives
from agentclaw.community.core.task.context_collab.l4_context_primitives.activity import (
    Activity,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ResourceBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.decision import (
    Decision,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventEnvelope,
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.message import (
    Message,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
    RoleBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.query import (
    Query,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)

SPEC_DICT = {
    "space": {"space_id": "space_workspace_001", "environment": "prod"},
    "principal_ref": {"principal_type": "human", "principal_id": "u_1001"},
    "method_kind": "Open + Group",
    "executor_ref": None,
    "objective": "完成上下文协作协议评审",
    "resource_refs": [
        {"resource_type": "forum_topic", "resource_id": "topic_001", "revision": None},
        {"resource_type": "repo", "resource_id": "repo_001", "revision": "3"},
    ],
    "product_kind": "public_discussion",
    "workflow_kind": "forum_public_discussion",
}

SNAPSHOT_DICT = {
    "objective": "完成上下文协作协议评审",
    "status": "in_progress",
    "principal_ref": {"principal_type": "human", "principal_id": "u_1001"},
    "executor_ref": {"agent_type": "bot", "agent_id": "bot_coding_001"},
    "method_kind": "Single",
    "progress": {
        "percent": 40,
        "completed_count": 2,
        "active_count": 1,
        "blocked_count": 0,
        "current_event_id": "evt_004",
    },
}


def _record(parent_event="evt_001"):
    return EventRecord(
        collab_context_id="collet_todo_001",
        event_id="evt_002",
        parent_event=parent_event,
        event_kind="user_interaction.posted",
        primitive_type="Message",
        actor_ref=PrincipalRef(principal_type="human", principal_id="u_1001"),
        visibility="workspace",
        payload={"message_kind": "user_interaction", "content": "补充一下上下文"},
        context_version_after=2,
        occurred_at=datetime(2026, 11, 10, 8, 30, 0),
    )


def test_status_vocabulary_matches_api_examples():
    assert [s.value for s in TaskStatus] == [
        "created",
        "in_progress",
        "reviewing",
        "blocked",
        "done",
        "cancelled",
    ]


def test_context_spec_round_trip_with_none_executor_ref():
    spec = ContextSpec.from_dict(SPEC_DICT)
    assert spec.executor_ref is None
    assert spec.method_kind == "Open + Group"
    assert spec.resource_refs == (
        ResourceRef(resource_type="forum_topic", resource_id="topic_001", revision=None),
        ResourceRef(resource_type="repo", resource_id="repo_001", revision="3"),
    )
    assert isinstance(spec.resource_refs, tuple)
    dumped = spec.to_dict()
    assert dumped == SPEC_DICT
    assert ContextSpec.from_dict(dumped) == spec


def test_context_spec_round_trip_with_executor_and_optional_profiles():
    data = dict(SPEC_DICT)
    data["executor_ref"] = {"agent_type": "bot", "agent_id": "bot_coding_001"}
    data["resource_refs"] = []
    data["product_kind"] = None
    data["workflow_kind"] = None
    spec = ContextSpec.from_dict(data)
    assert spec.executor_ref == AgentRef(agent_type="bot", agent_id="bot_coding_001")
    assert spec.resource_refs == ()
    assert spec.product_kind is None
    assert spec.workflow_kind is None
    assert ContextSpec.from_dict(spec.to_dict()) == spec


@pytest.mark.parametrize(
    "data",
    [
        {**SPEC_DICT, "method_kind": "swarm"},
        {**SPEC_DICT, "method_kind": ""},
        {**SPEC_DICT, "objective": ""},
        {**SPEC_DICT, "space": {}},
        {**SPEC_DICT, "resource_refs": "topic_001"},
    ],
)
def test_context_spec_rejects_invalid_fields(data):
    with pytest.raises(ContextCollabValidationError):
        ContextSpec.from_dict(data)


def test_progress_snapshot_defaults_and_to_dict():
    snapshot = ProgressSnapshot()
    assert snapshot.to_dict() == {
        "percent": 0,
        "completed_count": 0,
        "active_count": 0,
        "blocked_count": 0,
        "current_event_id": None,
    }
    full = ProgressSnapshot(
        percent=40,
        completed_count=2,
        active_count=1,
        blocked_count=0,
        current_event_id="evt_004",
    )
    assert full.to_dict() == SNAPSHOT_DICT["progress"]


def test_current_snapshot_round_trip():
    snapshot = CurrentSnapshot.from_dict(SNAPSHOT_DICT)
    assert snapshot.status == "in_progress"
    assert snapshot.progress == ProgressSnapshot(
        percent=40,
        completed_count=2,
        active_count=1,
        blocked_count=0,
        current_event_id="evt_004",
    )
    assert snapshot.to_dict() == SNAPSHOT_DICT
    assert CurrentSnapshot.from_dict(snapshot.to_dict()) == snapshot


@pytest.mark.parametrize("bad", ["", "paused", "ready", "active", "DONE", None])
def test_current_snapshot_from_dict_rejects_unknown_status(bad):
    data = dict(SNAPSHOT_DICT)
    data["status"] = bad
    with pytest.raises(ContextCollabValidationError):
        CurrentSnapshot.from_dict(data)


@pytest.mark.parametrize(
    "data",
    [
        {**SNAPSHOT_DICT, "method_kind": "swarm"},
        {**SNAPSHOT_DICT, "objective": ""},
        {**SNAPSHOT_DICT, "progress": None},
    ],
)
def test_current_snapshot_rejects_invalid_fields(data):
    with pytest.raises(ContextCollabValidationError):
        CurrentSnapshot.from_dict(data)


def test_current_snapshot_accepts_unknown_executor_absence():
    data = dict(SNAPSHOT_DICT)
    data["executor_ref"] = None
    snapshot = CurrentSnapshot.from_dict(data)
    assert snapshot.executor_ref is None
    assert CurrentSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_collet_holds_aggregate_state():
    spec = ContextSpec.from_dict(SPEC_DICT)
    snapshot = CurrentSnapshot.from_dict(SNAPSHOT_DICT)
    collet = Collet(
        collab_context_id="collet_todo_001",
        actor_ref=None,
        context_spec=spec,
        current_snapshot=snapshot,
        current_event_id="evt_004",
        context_version=4,
    )
    assert collet.context_spec == spec
    assert collet.current_snapshot == snapshot
    assert collet.context_version == 4


def test_event_record_to_envelope_matches_acc_contract():
    envelope = _record().to_envelope()
    assert isinstance(envelope, EventEnvelope)
    assert envelope.event_id == "evt_002"
    assert envelope.event_type == "user_interaction.posted"
    assert envelope.event_version == 1
    assert envelope.aggregate_ref == "collet_todo_001"
    assert envelope.aggregate_revision == 2
    assert envelope.causation_id == _record().parent_event
    assert envelope.payload == {"message_kind": "user_interaction", "content": "补充一下上下文"}


def test_event_record_from_envelope_reverse_constructor_round_trips():
    record = _record()
    rebuilt = EventRecord.from_envelope(
        record.to_envelope(),
        primitive_type=record.primitive_type,
        actor_ref=record.actor_ref,
        visibility=record.visibility,
        occurred_at=record.occurred_at,
    )
    assert rebuilt == record


def test_event_record_from_envelope_rejects_unsupported_version():
    record = _record()
    envelope = record.to_envelope()
    envelope.event_version = 2
    with pytest.raises(ContextCollabValidationError):
        EventRecord.from_envelope(
            envelope,
            primitive_type=record.primitive_type,
            actor_ref=record.actor_ref,
            visibility=record.visibility,
            occurred_at=record.occurred_at,
        )


def test_participant_round_trip_requires_principal_or_agent_ref():
    human = Participant.from_dict(
        {
            "participant_id": "part_u_1001",
            "principal_ref": {"principal_type": "human", "principal_id": "u_1001"},
        }
    )
    assert human.principal_ref == PrincipalRef(
        principal_type="human", principal_id="u_1001"
    )
    assert human.agent_ref is None
    assert Participant.from_dict(human.to_dict()) == human

    bot = Participant.from_dict(
        {
            "participant_id": "part_bot_001",
            "agent_ref": {"agent_type": "bot", "agent_id": "bot_coding_001"},
        }
    )
    assert bot.principal_ref is None
    assert bot.agent_ref == AgentRef(agent_type="bot", agent_id="bot_coding_001")
    assert Participant.from_dict(bot.to_dict()) == bot

    with pytest.raises(ContextCollabValidationError):
        Participant.from_dict({"participant_id": "part_none"})


def test_role_binding_round_trip():
    binding = RoleBinding.from_dict(
        {"role": "responsible_user", "participant_id": "part_u_1001"}
    )
    assert binding == RoleBinding(role="responsible_user", participant_id="part_u_1001")
    assert RoleBinding.from_dict(binding.to_dict()) == binding
    with pytest.raises(ContextCollabValidationError):
        RoleBinding.from_dict({"role": "", "participant_id": "part_u_1001"})


def test_activity_round_trip():
    activity = Activity.from_dict(
        {"activity_id": "act_001", "activity_kind": "task.progress.updated", "status": "completed"}
    )
    assert Activity.from_dict(activity.to_dict()) == activity
    with pytest.raises(ContextCollabValidationError):
        Activity.from_dict({"activity_id": "", "activity_kind": "x", "status": "y"})


def test_decision_round_trip():
    decision = Decision.from_dict(
        {
            "decision_id": "dec_001",
            "decision_kind": "task.accepted",
            "decided_by": {"principal_type": "human", "principal_id": "u_1001"},
        }
    )
    assert decision.decided_by == PrincipalRef(
        principal_type="human", principal_id="u_1001"
    )
    assert Decision.from_dict(decision.to_dict()) == decision
    with pytest.raises(ContextCollabValidationError):
        Decision.from_dict({"decision_id": "dec_001", "decision_kind": "", "decided_by": {}})


def test_message_semantic_constants_and_round_trip():
    from agentclaw.community.core.task.context_collab.l4_context_primitives import (
        message,
    )

    assert message.PRIMITIVE_TYPE_MESSAGE == "Message"
    assert message.MESSAGE_KIND_USER_INTERACTION == "user_interaction"
    assert message.MESSAGE_EVENT_KIND_USER_INTERACTION_POSTED == (
        "user_interaction.posted"
    )
    assert message.MESSAGE_CONTENT_FIELD == "content"

    msg = Message.from_dict(
        {"message_kind": "user_interaction", "content": "补充一下上下文"}
    )
    assert Message.from_dict(msg.to_dict()) == msg
    with pytest.raises(ContextCollabValidationError):
        Message.from_dict({"message_kind": "user_interaction", "content": ""})


def test_resource_binding_and_artifact_round_trip():
    binding = ResourceBinding.from_dict(
        {
            "resource_ref": {
                "resource_type": "repo",
                "resource_id": "repo_001",
                "revision": "3",
            },
            "grant_ref": None,
        }
    )
    assert binding.resource_ref == ResourceRef(
        resource_type="repo", resource_id="repo_001", revision="3"
    )
    assert binding.grant_ref is None
    assert ResourceBinding.from_dict(binding.to_dict()) == binding

    artifact = Artifact.from_dict(
        {"artifact_id": "art_001", "resource_binding": binding.to_dict()}
    )
    assert artifact.resource_binding == binding
    assert Artifact.from_dict(artifact.to_dict()) == artifact


def test_query_defaults_to_all_events():
    query = Query(collab_context_id="collet_todo_001")
    assert query.trunk_events is False
    full = Query(collab_context_id="collet_todo_001", trunk_events=True)
    assert full.trunk_events is True


def test_no_colletevent_classname_in_l4_domain_code():
    # spec §3.4.1:ColletEvent 不是 ACCS 正式对象名,目录和领域代码不得使用。
    for info in pkgutil.iter_modules(l4_context_primitives.__path__):
        module = importlib.import_module(
            f"{l4_context_primitives.__name__}.{info.name}"
        )
        assert info.name != "collet_event"
        assert "ColletEvent" not in dir(module)
