"""实际产品装配下的协议与存储集成测试,不是用户旅程验收。

观察 L6 实例/角色、L5 Open 录取、L4 事件/快照、真实 SQLite 和
A2A mock 边界,检查跨层关联、交付时序及拒绝操作无写入。仅替换
数据库与外部交付边界;用户视角闭环另见 test_task_commission_end_to_end.py。
"""

import json
from dataclasses import dataclass

import pytest
from injector import Binder, Injector, inject, singleton
from sqlalchemy import select

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.a2a_wire import (
    A2aMessage,
    AgentCard,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
    MockA2aDelivery,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
    AccsOperationResult,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    OpportunityState,
    ParticipationApplication,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
    ContextCollabRepositoryProtocol,
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE_REF,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskCommissionServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin

HUMAN = ActorContext(actor=PrincipalRef("human", "u_owner_001"), source="api")
BOT_ASSIGNEE = "bot_writer_001"
BOT = ActorContext(actor=PrincipalRef("bot", BOT_ASSIGNEE), source="api")
SPACE = SpaceRef(space_id="space_workspace_001", environment="test")
TITLE = "生成季度安全报告"
BODY = "汇总 Q3 安全事件,输出 Markdown 报告文件。"
ARTIFACT = "file://reports/2026-q3-security.md"


@dataclass(frozen=True)
class _DeliveryObservation:
    card: AgentCard
    message: A2aMessage
    envelope: AccsOperationEnvelope
    # Missing SQL state at send time would expose a premature dispatch.
    record: ContextStatusRecord | None
    applications: tuple[ParticipationApplication, ...]
    result: AccsOperationResult


class _RecordingDelivery(MockA2aDelivery):
    """只观察传输边界,保留 MockA2aDelivery 的回执与桥接簿记。"""

    @inject
    def __init__(
        self, repo: ContextCollabRepositoryProtocol, store: MethodStoreProtocol
    ) -> None:
        super().__init__()
        self._repo = repo
        self._store = store
        self.sent: list[_DeliveryObservation] = []

    def send_message(
        self, card: AgentCard, message: A2aMessage, envelope: AccsOperationEnvelope
    ) -> AccsOperationResult:
        collet_id = envelope.target_ref.object_id
        record = self._repo.get_current_status(collet_id)
        applications = tuple(
            self._store.list_applications(
                current_axes(), envelope.context.method_binding_ref.object_id, collet_id
            )
        )
        result = super().send_message(card, message, envelope)
        self.sent.append(
            _DeliveryObservation(card, message, envelope, record, applications, result)
        )
        return result


@pytest.fixture
def product_app(db) -> Injector:
    """使用实际产品安装与装配,每个用例独占 SQLite/MethodStore/L1 mock。"""

    def local_boundaries(binder: Binder) -> None:
        binder.bind(DatabasePlugin, to=db)
        binder.bind(A2aDeliveryPort, to=_RecordingDelivery, scope=singleton)

    return Injector([TaskPersistenceModule(), TaskModule(), local_boundaries])


def _publish(service):
    # 产品使用者只提供业务字段;不手工 prepare_product_spec、注册 Profile
    # 或构造 MethodBinding / ProductInstance,由已安装产品定义驱动落地。
    return service.publish_task(
        PublishTaskCommand(
            title=TITLE, body=BODY, space=SPACE, assignee_agent_id=BOT_ASSIGNEE
        ),
        HUMAN,
    )


def test_commission_protocol_and_persistence_integration(product_app, db, engine):
    """检查版本/角色/录取/交付关联,以及 Work 推进后的持久化一致性。"""
    service = product_app.get(TaskCommissionServiceProtocol)
    repo = product_app.get(ContextCollabRepositoryProtocol)
    store = product_app.get(MethodStoreProtocol)
    delivery = product_app.get(A2aDeliveryPort)
    catalog = product_app.get(ProductCatalog)
    assert isinstance(repo, ContextCollabRepository)
    assert isinstance(delivery, _RecordingDelivery)
    profile = catalog.get(
        TASK_COMMISSION_PROFILE_REF.profile_id, TASK_COMMISSION_PROFILE_REF.version
    )
    assert profile.ref == TASK_COMMISSION_PROFILE_REF
    assert profile.composition.method_kind == "Open"  # 指定 Bot 不等于 Single。

    # 1. 用户只调用产品入口;实例、版本、角色与方法全部由实际实现生成。
    created = _publish(service)
    collet_id = created.collet_id
    assert created.status == "created"
    assert created.context_version == 2  # task.created + 正文 Message。
    record = repo.get_current_status(collet_id)
    assert record is not None
    spec, instance = record.context_spec, record.product_instance
    assert instance is not None
    assert instance.collet_ref == collet_id
    assert instance.profile_ref == profile.ref
    assert instance.workflow_ref == profile.workflow.ref
    assert instance.domain_ref == profile.domain.ref
    assert instance.method_profiles == profile.composition.required_methods
    assert instance.governance == profile.governance
    assert instance.creation_event_ref == record.events[0].event_id
    assert instance.negotiation.accepted is True
    assert instance.negotiation.selected_profile == profile.ref
    assert instance.negotiation.mode == "local_exact_match"  # 非远端协商。
    assert instance.negotiation.missing_capabilities == ()
    assert spec.product_profile_version == profile.ref.version
    assert spec.workflow_profile_version == profile.workflow.ref.version
    assert spec.method_kind == profile.composition.method_kind
    assert spec.workflow_kind == profile.workflow.ref.profile_id

    participants = {item.participant_id: item for item in spec.participants}
    roles = {
        item.role: participants[item.participant_id] for item in spec.role_bindings
    }
    declared_roles = {role.name: role for role in profile.scenario_roles}
    assert len(participants) == len(roles) == 2
    assert roles[declared_roles["owner"].l4_role].principal_ref == HUMAN.actor
    assert roles[declared_roles["assignee"].l4_role].agent_ref == AgentRef(
        "bot", BOT_ASSIGNEE
    )
    assert instance.role_binding_refs == tuple(
        role_binding_ref(collet_id, item) for item in spec.role_bindings
    )

    # 2. 真实 Open 服务完成发布人代投/审核/录取,没有伪造 Bot 主动申请。
    axes = current_axes()
    [binding] = store.list_bindings(axes, collet_id)
    binding_id = binding.method_binding_id
    assert instance.method_binding_refs == (binding_id,)
    assert binding_id == top_level_binding_id(collet_id)
    assert binding.state is BindingState.ACTIVE
    assert binding.method_names == ("Open",)
    assert binding.profile_versions == {"Open": "0.2"}
    assert binding.role_binding_refs == instance.role_binding_refs
    [opportunity] = store.list_opportunities(axes, binding_id, collet_id)
    policy = profile.require_publication().admission
    assert opportunity.state is OpportunityState.OPEN
    assert opportunity.scope_ref == collet_id
    assert opportunity.capacity == policy.capacity == 1
    assert opportunity.participation_type is policy.participation_type
    assert opportunity.participation_type is ParticipationType.SINGLE_CONTRIBUTION
    [application] = store.list_applications(axes, binding_id, collet_id)
    assert application.state is ApplicationState.ADMITTED
    assert application.opportunity_ref == opportunity.opportunity_id
    assert application.applicant_agent_ref == BOT_ASSIGNEE
    assert application.accountable_principal_ref == HUMAN.actor.principal_id
    assert application.transitioned_by == HUMAN.actor.principal_id
    [decision] = store.list_decisions(axes, binding_id, collet_id)
    assert decision.application_ref == application.application_id
    assert decision.admitted_scope_ref == collet_id

    # 3. 走真实 A2aTaskDispatch,在 L1 边界观察发送当时的入库与录取事实。
    [sent] = delivery.sent
    assert sent.record is not None
    assert sent.record.product_instance == instance
    assert sent.record.collet.version == 2
    assert sent.record.current_snapshot.status == "created"
    assert [event.event_kind for event in sent.record.events] == [
        "task.created",
        "user_interaction.posted",
    ]
    assert sent.applications == (application,)  # 发送之前已经 ADMITTED。
    envelope = sent.envelope
    assert sent.card.agent_id == BOT_ASSIGNEE
    assert AccsOperationEnvelope.from_dict(sent.message.parts[0].body) == envelope
    assert sent.message.parts[0].part_kind == "ENVELOPE"
    assert (
        envelope.layer,
        envelope.operation_family,
        envelope.action,
        envelope.mode,
    ) == ("L4", "message", "publish", "EVENT")
    assert envelope.payload == {
        "title": TITLE,
        "body": BODY,
        "assignee_agent_id": BOT_ASSIGNEE,
    }
    assert envelope.context.caller.identity_type == "AGENT"
    assert envelope.context.accountable_principal_ref.identity_type == "PRINCIPAL"
    assert envelope.context.product_profile_ref.object_type == "ProductProfile"
    assert envelope.context.method_binding_ref.object_type == "MethodBinding"
    assert envelope.target_ref.object_type == "Collet"
    assert envelope.target_ref.version == str(created.context_version)
    assert (
        envelope.context.accountable_principal_ref.identity_id
        == HUMAN.actor.principal_id
    )
    assert (
        envelope.context.product_profile_ref.object_id
        == instance.profile_ref.profile_id
    )
    assert envelope.context.product_profile_ref.version == str(
        instance.profile_ref.version
    )
    assert envelope.context.method_binding_ref.object_id == binding_id
    assert sent.result.status == "ACCEPTED"  # 送达受理 != 任务 done。
    [bridge] = delivery.bridge_records()
    assert bridge.accs_object_ref.object_id == envelope.operation_id
    assert bridge.a2a_object_id == sent.message.message_id
    assert (bridge.a2a_object_type, bridge.direction) == ("MESSAGE", "OUTBOUND")

    # 4. Bot 从通知取得上下文 ID,经产品查询和 Work API 执行,不是直改仓储。
    bot_context_id = envelope.target_ref.object_id
    assert bot_context_id == collet_id
    bot_detail = service.detail_task(bot_context_id)
    assert bot_detail.status == "created"
    assert bot_detail.body.content == BODY
    started = service.start_task(TaskAdvanceCommand(bot_context_id), BOT)
    assert (started.status, started.context_version) == ("in_progress", 3)
    progressing = service.report_progress(
        TaskAdvanceCommand(bot_context_id, note="初稿完成,校对中", percent=60), BOT
    )
    assert (progressing.status, progressing.context_version) == ("in_progress", 4)
    progress_detail = service.detail_task(collet_id)
    assert progress_detail.status == "in_progress"
    assert progress_detail.events[-1]["payload"] == {
        "note": "初稿完成,校对中",
        "percent": 60,
    }
    assert progress_detail.events[-1]["actor_id"] == BOT_ASSIGNEE
    submitted = service.submit_task(
        TaskAdvanceCommand(bot_context_id, note="报告已产出", artifacts=[ARTIFACT]), BOT
    )
    assert (submitted.status, submitted.context_version) == ("reviewing", 5)
    review_detail = service.detail_task(collet_id)
    assert review_detail.status == "reviewing"
    assert review_detail.events[-1]["payload"]["artifacts"] == [ARTIFACT]

    # 5. 用户查询列表/详情并验收;G4 裁决 done,L6 实例只保存选择/证据引用。
    [summary] = service.list_tasks()
    assert (summary.collet_id, summary.title, summary.initiator_id) == (
        collet_id,
        TITLE,
        HUMAN.actor.principal_id,
    )
    assert summary.message_count == 1
    accepted = service.accept_task(TaskAdvanceCommand(collet_id, note="通过"), HUMAN)
    assert (accepted.status, accepted.context_version) == ("done", 6)
    final_detail = service.detail_task(collet_id)
    assert final_detail.title == TITLE
    assert final_detail.status == "done"
    assert final_detail.body.content == BODY
    assert final_detail.body.event_id == created.body_event_id
    assert [event["event_kind"] for event in final_detail.events] == [
        "task.created",
        "user_interaction.posted",
        "task.started",
        "task.progress.updated",
        "task.submitted",
        "task.accepted",
    ]
    assert final_detail.events[-2]["payload"]["artifacts"] == [ARTIFACT]
    assert final_detail.events[-1]["actor_id"] == HUMAN.actor.principal_id

    # 独立 repository/SQL 会话读到持久事实,而不是 fake 的 _status 字典。
    reloaded = ContextCollabRepository(db, product_catalog=catalog).get_current_status(
        collet_id
    )
    assert reloaded.product_instance == instance  # 没有 L6 的第二套生命周期。
    assert reloaded.current_snapshot.status == "done"
    assert reloaded.collet.version == 6
    assert [event.event_id for event in reloaded.events] == [
        event["event_id"] for event in final_detail.events
    ]
    with engine.connect() as connection:
        raw_spec = connection.scalar(
            select(ContextCollabColletModel.context_spec).where(
                ContextCollabColletModel.collab_context_id == collet_id
            )
        )
    assert json.loads(raw_spec)["product_instance"] == instance.to_dict()
    assert len(delivery.sent) == 1  # Work 推进没有重新发布/录取/送达。
    assert store.list_bindings(axes, collet_id) == [binding]
    assert store.list_opportunities(axes, binding_id, collet_id) == [opportunity]
    assert store.list_applications(axes, binding_id, collet_id) == [application]
    assert store.list_decisions(axes, binding_id, collet_id) == [decision]


def test_owner_rejects_and_bot_resubmits_in_same_product_instance(product_app):
    """退回是已选 workflow 的补偿动作,复用同一产品实例/方法/录取。"""
    service = product_app.get(TaskCommissionServiceProtocol)
    repo = product_app.get(ContextCollabRepositoryProtocol)
    delivery = product_app.get(A2aDeliveryPort)
    created = _publish(service)
    collet_id = created.collet_id
    instance = repo.get_current_status(collet_id).product_instance
    service.start_task(TaskAdvanceCommand(collet_id), BOT)
    service.submit_task(TaskAdvanceCommand(collet_id, artifacts=[ARTIFACT]), BOT)
    rejected = service.reject_task(
        TaskAdvanceCommand(collet_id, note="补充修复建议"), HUMAN
    )
    assert rejected.status == "in_progress"
    assert (
        service.detail_task(collet_id).events[-1]["payload"]["note"] == "补充修复建议"
    )
    revised = "file://reports/2026-q3-security-v2.md"
    service.report_progress(TaskAdvanceCommand(collet_id, percent=100), BOT)
    service.submit_task(TaskAdvanceCommand(collet_id, artifacts=[revised]), BOT)
    service.accept_task(TaskAdvanceCommand(collet_id), HUMAN)
    detail = service.detail_task(collet_id)
    assert detail.status == "done"
    submissions = [
        event for event in detail.events if event["event_kind"] == "task.submitted"
    ]
    assert [event["payload"]["artifacts"] for event in submissions] == [
        [ARTIFACT],
        [revised],
    ]
    record = repo.get_current_status(collet_id)
    assert (record.collet.version, len(record.events)) == (8, 8)
    assert record.product_instance == instance
    assert len(delivery.sent) == 1


@pytest.mark.parametrize(
    "stage,action,actor,artifacts,match",
    [
        ("created", "submit_task", BOT, [ARTIFACT], "cannot advance"),
        ("in_progress", "submit_task", HUMAN, [ARTIFACT], "designated executor"),
        (
            "in_progress",
            "submit_task",
            ActorContext(actor=PrincipalRef("human", BOT_ASSIGNEE), source="api"),
            [ARTIFACT],
            "only the principal",
        ),
        ("in_progress", "submit_task", BOT, [], "no artifacts"),
        ("reviewing", "accept_task", BOT, [], "not the responsible principal"),
        ("reviewing", "reject_task", BOT, [], "not the responsible principal"),
        ("in_progress", "cancel_task", BOT, [], "not the responsible principal"),
        ("done", "report_progress", BOT, [], "cannot advance"),
    ],
)
def test_product_contract_rejects_invalid_use_without_writes(
    product_app, stage, action, actor, artifacts, match
):
    """真实 G1/G4 拒绝越序、身份冒充、缺少产物、越权验收和终态写入。"""
    service = product_app.get(TaskCommissionServiceProtocol)
    repo = product_app.get(ContextCollabRepositoryProtocol)
    collet_id = _publish(service).collet_id
    if stage != "created":
        service.start_task(TaskAdvanceCommand(collet_id), BOT)
    if stage in {"reviewing", "done"}:
        service.submit_task(TaskAdvanceCommand(collet_id, artifacts=[ARTIFACT]), BOT)
    if stage == "done":
        service.accept_task(TaskAdvanceCommand(collet_id), HUMAN)
    before = repo.get_current_status(collet_id)
    command = TaskAdvanceCommand(collet_id, artifacts=artifacts)
    with pytest.raises(ContextCollabValidationError, match=match):
        getattr(service, action)(command, actor)
    assert repo.get_current_status(collet_id) == before
    assert len(product_app.get(A2aDeliveryPort).sent) == 1


@pytest.mark.parametrize("stage", ["created", "in_progress", "reviewing"])
def test_owner_cancels_and_bot_cannot_continue(product_app, stage):
    """取消仍由 L4/G4 收敛,不会删除历史、重建产品或自动撤销 Open 录取。"""
    service = product_app.get(TaskCommissionServiceProtocol)
    repo = product_app.get(ContextCollabRepositoryProtocol)
    collet_id = _publish(service).collet_id
    if stage != "created":
        service.start_task(TaskAdvanceCommand(collet_id), BOT)
    if stage == "reviewing":
        service.submit_task(TaskAdvanceCommand(collet_id, artifacts=[ARTIFACT]), BOT)
    instance = repo.get_current_status(collet_id).product_instance
    cancelled = service.cancel_task(
        TaskAdvanceCommand(collet_id, note="需求已撤回"), HUMAN
    )
    assert cancelled.status == "cancelled"
    before = repo.get_current_status(collet_id)
    with pytest.raises(ContextCollabValidationError, match="cannot advance"):
        service.report_progress(TaskAdvanceCommand(collet_id, percent=80), BOT)
    assert repo.get_current_status(collet_id) == before
    assert before.product_instance == instance
    detail = service.detail_task(collet_id)
    assert detail.status == "cancelled"
    assert detail.events[-1]["event_kind"] == "task.cancelled"
    assert detail.events[-1]["payload"] == {"note": "需求已撤回"}
    assert len(product_app.get(A2aDeliveryPort).sent) == 1
    [sent] = product_app.get(A2aDeliveryPort).sent
    assert (
        tuple(
            product_app.get(MethodStoreProtocol).list_applications(
                current_axes(), instance.method_binding_refs[0], collet_id
            )
        )
        == sent.applications
    )  # Task 取消不隐式撤销 L5 录取。
