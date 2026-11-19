"""L4 领域对象纯构造单测(Task 3)。

覆盖 ``TaskStatus`` 状态词汇表、普通 creation facts 的规范化与校验、
``CurrentSnapshot`` / ``ProgressSnapshot`` 的 ``to_dict`` / ``from_dict`` round-trip 与校验拒绝、
六个薄值对象(participants / activity / decision / message / artifact /
query)的构造与序列化,以及 ``EventRecord`` 与 ACCS ``EventEnvelope``
(spec §3.4.1)的双向适配。不触碰数据库,纯内存值对象。
"""

import importlib
import pkgutil
from datetime import datetime

import pytest
from agentclaw.community.core.task.context_collab import l4_context_primitives
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.views import (
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.activity import (
    Activity,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ResourceBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
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
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    decode_creation_facts,
)

CREATION_FACTS = {
    "objective": "完成上下文协作协议评审",
    "resource_refs": [
        {"resource_type": "forum_topic", "resource_id": "topic_001", "revision": None},
        {"resource_type": "repo", "resource_id": "repo_001", "revision": "3"},
    ],
    "method_kind": "Open + Group",
    "executor_ref": None,
    "product_kind": "public_discussion",
    "workflow_kind": "forum_public_discussion",
    "product_profile_ref": None,
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


@pytest.mark.parametrize("composition", ["Open + Group", "Open + Single", "Open + Swarm"])
def test_creation_facts_normalize_with_none_executor_ref(composition):
    facts = decode_creation_facts({**CREATION_FACTS, "method_kind": composition})
    assert facts["executor_ref"] is None
    assert facts["method_kind"] == composition
    assert facts["resource_refs"] == CREATION_FACTS["resource_refs"]
    assert facts["product_profile_ref"] is None
    assert decode_creation_facts(facts) == facts


def test_creation_facts_normalize_with_executor_and_profile():
    data = dict(CREATION_FACTS)
    data["executor_ref"] = {"agent_type": "bot", "agent_id": "bot_coding_001"}
    data["resource_refs"] = []
    data["product_kind"] = "task_commission"
    data["workflow_kind"] = "task_commission_goal"
    data["product_profile_ref"] = {"profile_id": "task_commission", "version": 2}
    facts = decode_creation_facts(data)
    assert facts["executor_ref"] == data["executor_ref"]
    assert facts["resource_refs"] == []
    assert facts["product_profile_ref"] == data["product_profile_ref"]
    assert decode_creation_facts(facts) == facts


@pytest.mark.parametrize(
    "data",
    [
        {**CREATION_FACTS, "unknown": True},
        {**CREATION_FACTS, "method_kind": "swarm"},
        {**CREATION_FACTS, "method_kind": ""},
        {**CREATION_FACTS, "method_kind": None},
        {**CREATION_FACTS, "method_kind": "Swarm + Open"},
        {**CREATION_FACTS, "objective": ""},
        {**CREATION_FACTS, "resource_refs": "topic_001"},
        {**CREATION_FACTS, "executor_ref": "bot_coding_001"},
        {**CREATION_FACTS, "product_kind": ""},
    ],
)
def test_creation_facts_reject_invalid_fields(data):
    with pytest.raises(ContextCollabValidationError):
        decode_creation_facts(data)


def test_creation_facts_reject_mismatched_profile():
    data = dict(CREATION_FACTS)
    data["product_profile_ref"] = {"profile_id": "task_commission", "version": 1}
    with pytest.raises(ContextCollabValidationError):
        decode_creation_facts(data)


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
        {**SNAPSHOT_DICT, "method_kind": ""},
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
    initiator = PrincipalRef(principal_type="human", principal_id="u_1001")
    scope = SpaceRef(space_id="space_workspace_001", environment="prod")
    collet = Collet(
        collet_id="collet_todo_001",
        initiator_ref=initiator,
        state=ColletState.ACTIVE,
        scope=scope,
        version=4,
    )
    # Collet 只持有 L4 身份、scope 和生命周期；creation facts 不挂在聚合根上。
    assert collet.initiator_ref == initiator
    assert collet.state is ColletState.ACTIVE
    assert collet.scope == scope
    assert not hasattr(collet, "current_event_id")
    assert collet.version == 4
    assert Collet.from_dict(collet.to_dict()) == collet


def test_event_record_to_envelope_matches_acc_contract():
    envelope = _record().to_envelope()
    assert isinstance(envelope, EventEnvelope)
    assert envelope.event_id == "evt_002"
    assert envelope.event_type == "user_interaction.posted"
    assert envelope.event_version == 1
    assert envelope.aggregate_ref == "collet_todo_001"
    assert envelope.aggregate_revision == 2
    assert envelope.causation_id == _record().parent_event
    assert envelope.payload == {
        "message_kind": "user_interaction",
        "content": "补充一下上下文",
    }


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
        {
            "activity_id": "act_001",
            "activity_kind": "task.progress.updated",
            "status": "completed",
        }
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
        Decision.from_dict(
            {"decision_id": "dec_001", "decision_kind": "", "decided_by": {}}
        )


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
