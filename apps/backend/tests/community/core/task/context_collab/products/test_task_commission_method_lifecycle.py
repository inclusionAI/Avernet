"""任务代办发布驱动的真 Open 方法生命周期单测(spec §9 增量裁决)。

核心裁决:``publish_task`` 不再只是投影顶层单值 Open binding,而是
经方法编排缝驱动 L5 Open 方法服务四步真生命周期——
``publish_opportunity → submit_application → screen → admit``,
指定执行 bot 至 ADMITTED。四个测试面:

- 真链路驱动(真适配器+真 OpenMethodService):发布后方法 store 末端
  形态 = OPEN 机会(SINGLE_CONTRIBUTION/capacity=1)+ ADMITTED 申请
  (applicant=指定 bot/accountable=发布人)+ AdmissionDecision;
- 编排缝次序:方法面完成先于交付缝送达(bot 在生命周期完成后才被告知);
- 失败如实上抛:方法面抛错 → publish 原样上抛,Collet 已持久不回滚,
  正文/送达不发生(screen/admit 中途失败的 OPEN 机会孤儿行是 P1
  接受的中间态,不做补偿——spec §9);
- 校验前置:空字段在方法面之前即拒。

非 owner 拒绝用例不会出现在产品面:产品是系统编排,四步恒用发布人
ActorContext,非 owner 拿不到方法面入口(G1 check_create 先挡);
该守卫的覆盖住在 test_open_profile.py / test_method_authorization.py。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    OpportunityState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    AssigneeAdmissionCommand,
    AssigneeAdmissionReceipt,
    PublishTaskCommand,
    TaskDispatchPort,
    TaskMethodOpsPort,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    current_axes,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.services.open_method_service import (
    OpenMethodService,
)
from agentclaw.community.di.modules.task_commission_method_ops_adapter import (
    TaskCommissionOpenMethodAdapter,
)
from tests.community.core.task.context_collab.products.test_task_commission_publish import (
    ASSIGNEE,
    BODY,
    SPACE,
    TITLE,
    _FakeRepo,
    _actor,
    _publish_command,
)


class _SequenceDispatchPort(TaskDispatchPort):
    """交付缝捕获 + 全局次序记录(method_ops → dispatch)。"""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.dispatched = []

    def dispatch_task_info(self, info) -> None:
        self._calls.append("dispatch")
        self.dispatched.append(info)


class _SpyMethodOpsPort(TaskMethodOpsPort):
    """方法编排缝间谍:记录命令与调用者,不打真生命周期。"""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.commands = []

    def admit_designated_assignee(self, command, context):
        self._calls.append("method_ops")
        self.commands.append((command, context))
        return AssigneeAdmissionReceipt(
            opportunity_id="opp_fake",
            application_id="app_fake",
            decision_id="dec_fake",
            state="ADMITTED",
        )


class _ExplodingMethodOpsPort(TaskMethodOpsPort):
    """方法编排缝爆炸件:方法面失败须原样穿透发布编排。"""

    def admit_designated_assignee(self, command, context):
        raise ContextCollabValidationError("method ops boom")


def _real_method_port(repo, store):
    """真适配器 + 真 OpenMethodService(四件套的真后半段)。"""
    return TaskCommissionOpenMethodAdapter(
        open_ops=OpenMethodService(
            store=store,
            authorizer=StructuralMethodOpsAuthorizer(),
            collab_repo=repo,
        )
    )


def _service(repo=None, store=None, method_port=None, dispatch_port=None):
    repo = repo or _FakeRepo()
    store = store or InMemoryMethodStore()
    dispatch = dispatch_port or _SequenceDispatchPort([])
    service = TaskCommissionService(
        collab_repo=repo,
        authorizer=StructuralContextCollabAuthorizer(),
        method_store=store,
        method_ops_port=(
            method_port
            if method_port is not None
            else _real_method_port(repo, store)
        ),
        dispatch_port=dispatch,
    )
    return service, repo, store, dispatch


class TestPublishDrivesOpenMethodLifecycle:
    def test_publish_leaves_admitted_method_lifecycle_in_store(self):
        service, repo, store, dispatch = _service()
        view = service.publish_task(_publish_command(), _actor())

        axes = current_axes()
        binding_id = top_level_binding_id(view.collet_id)

        # 顶层绑定仍在,且方法面没有动它。
        binding = store.get_binding(axes, binding_id, view.collet_id)
        assert binding is not None
        assert binding.state is BindingState.ACTIVE
        assert binding.method_names == ("Open",)

        # 机会:恰一行,OPEN,SINGLE_CONTRIBUTION,capacity=1,scope=collet。
        opportunities = store.list_opportunities(
            axes, binding_id, view.collet_id
        )
        assert len(opportunities) == 1
        opportunity = opportunities[0]
        assert opportunity.state is OpportunityState.OPEN
        assert opportunity.participation_type is (
            ParticipationType.SINGLE_CONTRIBUTION
        )
        assert opportunity.capacity == 1
        assert opportunity.scope_ref == view.collet_id
        assert opportunity.closes_at is None
        assert opportunity.method_binding_id == binding_id
        assert opportunity.transitioned_by == "u_1001"

        # 申请:恰一行,ADMITTED,applicant=指定 bot,责任主体=发布人。
        applications = store.list_applications(
            axes, binding_id, view.collet_id
        )
        assert len(applications) == 1
        application = applications[0]
        assert application.state is ApplicationState.ADMITTED
        assert application.applicant_agent_ref == ASSIGNEE
        assert application.accountable_principal_ref == "u_1001"
        assert application.opportunity_ref == opportunity.opportunity_id

        # 录取裁决:恰一行,对 collet 取向,SINGLE_CONTRIBUTION 无产物行。
        decisions = store.list_decisions(axes, binding_id, view.collet_id)
        assert len(decisions) == 1
        decision = decisions[0]
        assert decision.application_ref == application.application_id
        assert decision.admitted_scope_ref == view.collet_id
        assert decision.resulting_binding_refs == ()

        # 生命周期完成后 bot 才被告知(送达在方法面之后)。
        assert len(dispatch.dispatched) == 1
        assert dispatch.dispatched[0].collet_id == view.collet_id


class TestSeamOrderingAndCommand:
    def test_method_ops_precedes_dispatch_with_publisher_actor(self):
        calls: list[str] = []
        port = _SpyMethodOpsPort(calls)
        dispatch = _SequenceDispatchPort(calls)
        service, repo, _, _ = _service(
            method_port=port, dispatch_port=dispatch
        )

        view = service.publish_task(_publish_command(), _actor())

        # 方法面恰调一次,命令三字段齐,调用者是发布人(系统编排裁决)。
        assert len(port.commands) == 1
        command, context = port.commands[0]
        assert isinstance(command, AssigneeAdmissionCommand)
        assert command.collab_context_id == view.collet_id
        assert command.assignee_agent_id == ASSIGNEE
        assert command.accountable_principal_id == "u_1001"
        assert context.actor.principal_id == "u_1001"

        # 次序:方法生命周期先完成,交付缝送达在后。
        assert calls == ["method_ops", "dispatch"]

        # view 形状不变(receipt P1 不进 view,方法 store 行即持久结局)。
        assert view.status == "created"
        assert view.title == TITLE
        assert view.context_version == 2


class TestMethodOpsFailurePropagates:
    def test_failure_raises_and_collet_stays_persisted(self):
        calls: list[str] = []
        dispatch = _SequenceDispatchPort(calls)
        service, repo, _, _ = _service(
            method_port=_ExplodingMethodOpsPort(),
            dispatch_port=dispatch,
        )

        with pytest.raises(
            ContextCollabValidationError, match="method ops boom"
        ):
            service.publish_task(_publish_command(), _actor())

        # Collet 已持久不回滚(创建事件已在);正文未追加;未送达。
        assert len(repo.create_calls) == 1
        assert repo.append_calls == []
        assert dispatch.dispatched == []


class TestPublishValidationPrecedesMethodOps:
    def test_blank_body_rejected_before_method_ops(self):
        calls: list[str] = []
        port = _SpyMethodOpsPort(calls)
        service, repo, _, _ = _service(method_port=port)

        with pytest.raises(ContextCollabValidationError):
            service.publish_task(
                PublishTaskCommand(
                    title=TITLE, body="  ", space=SPACE,
                    assignee_agent_id=ASSIGNEE,
                ),
                _actor(),
            )

        assert port.commands == []
        assert repo.create_calls == []
