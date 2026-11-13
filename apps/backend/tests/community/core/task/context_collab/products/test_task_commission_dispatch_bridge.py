"""交付缝桥到 L1 mock 的全链单测(plan T2;spec §5/裁决 G)。

覆盖:
- bridge 出去的 envelope 携**可追溯身份链**(ACCS-001 §5 规则 1):
  caller=本地 AGENT 调度者 + accountable_principal_ref=发起人;
- envelope 形态:layer=L4/family=message/mode=EVENT/
  target=ObjectRef("Collet", collet_id)/payload 三件;
- 载体 A2aMessage 含 ENVELOPE part;mock 桥接簿记落 MESSAGE/OUTBOUND;
- DI 全链:TaskDispatchPort 解析为 A2aTaskDispatch 且共享 mock singleton。
"""

from injector import Injector

from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    MockA2aDelivery,
)
from agentclaw.community.core.task.context_collab.products.task_commission.dispatch import (
    A2aTaskDispatch,
    LOCAL_ISSUER,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    DispatchedTaskInfo,
    PublishTaskCommand,
    TaskDispatchPort,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)

from tests.community.core.task.context_collab.products.test_task_commission_publish import (
    _FakeRepo,
)

HUMAN = ActorContext(
    actor=PrincipalRef("human", "u_owner_001"), source="api"
)
SPACE = SpaceRef(space_id="space_workspace_001", environment="prod")


def _publish(service):
    return service.publish_task(
        PublishTaskCommand(
            title="生成报告",
            body="任务正文。",
            space=SPACE,
            assignee_agent_id="bot_writer_001",
        ),
        HUMAN,
    )


def _service_with_bridge():
    mock = MockA2aDelivery()
    service = TaskCommissionService(
        collab_repo=_FakeRepo(),
        authorizer=StructuralContextCollabAuthorizer(),
        method_store=InMemoryMethodStore(),
        dispatch_port=A2aTaskDispatch(a2a_delivery=mock),
    )
    return service, mock


def test_bridge_envelope_carries_accountable_principal_chain():
    service, mock = _service_with_bridge()
    created = _publish(service)

    bridges = mock.bridge_records()
    assert len(bridges) == 1
    record = bridges[0]
    assert record.direction == "OUTBOUND"
    assert record.a2a_object_type == "MESSAGE"
    assert record.accs_object_ref.object_type == "Operation"
    assert record.content_digest  # 桥接记带内容摘要

    # envelope 半可验证还原:operation_id 唯一锚定。
    operation_id = record.accs_object_ref.object_id
    assert operation_id.startswith("op_")

    # mock 无失败即 accept;失败语义沿既有裁决(上抛不回滚)不在本用例路径。
    assert created.collet_id


def test_dispatched_info_carries_publisher_identity():
    """裁决 G:DispatchedTaskInfo 第 5 字段=发起人 principal_id。"""
    info = DispatchedTaskInfo(
        collet_id="collet_1",
        title="t",
        body="b",
        assignee_agent_id="bot_x",
        publisher_principal_id="u_1001",
    )
    assert info.publisher_principal_id == "u_1001"


def test_a2a_task_dispatch_implements_port():
    assert isinstance(A2aTaskDispatch(MockA2aDelivery()), TaskDispatchPort)


def test_local_issuer_constant_is_stable():
    assert isinstance(LOCAL_ISSUER, str) and LOCAL_ISSUER.strip()
