"""L2/L3/L5/L6 引用值对象与枚举纯构造单测(Task 2)。

覆盖 ``MethodKind`` wire 值接受/拒绝、``PrincipalRef`` / ``AgentRef`` /
``SpaceRef`` / ``ResourceRef`` 的 ``from_dict`` round-trip 与缺字段拒绝、
``ProductProfile`` / ``WorkflowKind`` 四个合法值,以及错误类继承关系。
"""
import pytest

from agentclaw.community.core.errors import (
    Conflict,
    NotFound,
    ValidationError,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l6_products.profiles import (
    ProductProfile,
    WorkflowKind,
)


@pytest.mark.parametrize("bad", ["", "Swarm", "open+group", "single"])
def test_method_kind_rejects_unknown_wire_values(bad):
    with pytest.raises(ContextCollabValidationError):
        MethodKind.from_wire(bad)


def test_method_kind_accepts_spec_values():
    assert MethodKind.from_wire("Single") is MethodKind.SINGLE
    assert MethodKind.from_wire("Open + Group") is MethodKind.OPEN_GROUP


def test_principal_ref_round_trip():
    ref = PrincipalRef.from_dict(
        {"principal_type": "human", "principal_id": "u_1001"}
    )
    assert ref == PrincipalRef(principal_type="human", principal_id="u_1001")
    dumped = ref.to_dict()
    assert dumped == {"principal_type": "human", "principal_id": "u_1001"}
    assert PrincipalRef.from_dict(dumped) == ref


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"principal_type": "human"},
        {"principal_id": "u_1001"},
        {"principal_type": "", "principal_id": "u_1001"},
        {"principal_type": "human", "principal_id": ""},
    ],
)
def test_principal_ref_rejects_missing_or_empty_fields(data):
    with pytest.raises(ContextCollabValidationError):
        PrincipalRef.from_dict(data)


def test_agent_ref_round_trip():
    ref = AgentRef.from_dict({"agent_type": "bot", "agent_id": "bot_coding_001"})
    assert ref == AgentRef(agent_type="bot", agent_id="bot_coding_001")
    dumped = ref.to_dict()
    assert dumped == {"agent_type": "bot", "agent_id": "bot_coding_001"}
    assert AgentRef.from_dict(dumped) == ref


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"agent_type": "bot"},
        {"agent_id": "bot_coding_001"},
        {"agent_type": "", "agent_id": "bot_coding_001"},
        {"agent_type": "bot", "agent_id": ""},
    ],
)
def test_agent_ref_rejects_missing_or_empty_fields(data):
    with pytest.raises(ContextCollabValidationError):
        AgentRef.from_dict(data)


def test_space_ref_round_trip():
    ref = SpaceRef.from_dict(
        {"space_id": "space_workspace_001", "environment": "prod"}
    )
    assert ref == SpaceRef(space_id="space_workspace_001", environment="prod")
    dumped = ref.to_dict()
    assert dumped == {"space_id": "space_workspace_001", "environment": "prod"}
    assert SpaceRef.from_dict(dumped) == ref


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"space_id": "space_workspace_001"},
        {"environment": "prod"},
        {"space_id": "", "environment": "prod"},
        {"space_id": "space_workspace_001", "environment": ""},
    ],
)
def test_space_ref_rejects_missing_or_empty_fields(data):
    with pytest.raises(ContextCollabValidationError):
        SpaceRef.from_dict(data)


def test_resource_ref_round_trip():
    ref = ResourceRef.from_dict(
        {"resource_type": "forum_topic", "resource_id": "topic_001", "revision": "3"}
    )
    assert ref == ResourceRef(
        resource_type="forum_topic", resource_id="topic_001", revision="3"
    )
    dumped = ref.to_dict()
    assert dumped == {
        "resource_type": "forum_topic",
        "resource_id": "topic_001",
        "revision": "3",
    }
    assert ResourceRef.from_dict(dumped) == ref


def test_resource_ref_revision_is_optional():
    ref = ResourceRef.from_dict(
        {"resource_type": "repo", "resource_id": "repo_001"}
    )
    assert ref.revision is None
    assert ResourceRef.from_dict(ref.to_dict()) == ref


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"resource_type": "forum_topic"},
        {"resource_id": "topic_001"},
        {"resource_type": "", "resource_id": "topic_001"},
        {"resource_type": "forum_topic", "resource_id": ""},
    ],
)
def test_resource_ref_rejects_missing_or_empty_fields(data):
    with pytest.raises(ContextCollabValidationError):
        ResourceRef.from_dict(data)


def test_product_profile_covers_spec_values():
    assert ProductProfile.TEAM_TODO.value == "team_todo"
    assert ProductProfile.PUBLIC_DISCUSSION.value == "public_discussion"


def test_workflow_kind_covers_spec_values():
    assert WorkflowKind.TEAM_TODO_EXECUTION.value == "team_todo_execution"
    assert (
        WorkflowKind.FORUM_PUBLIC_DISCUSSION.value
        == "forum_public_discussion"
    )


def test_context_collab_error_hierarchy():
    assert issubclass(ContextCollabValidationError, ValidationError)
    assert issubclass(ContextCollabNotFoundError, NotFound)
    assert issubclass(ContextCollabVersionConflict, Conflict)
    assert issubclass(ContextCollabParentEventError, ValidationError)
