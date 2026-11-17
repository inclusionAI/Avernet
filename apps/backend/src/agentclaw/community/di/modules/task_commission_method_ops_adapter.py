"""任务代办方法编排缝适配器:产品窄端口 → L5 Open 方法服务。

产品白名单(乙案)不允许 products 直连 ``method_service_api`` /
``l5_methods.operations.open``;本适配器住 DI 层(不受产品
import 法约束),把产品的 ``TaskMethodOpsPort`` 四步翻译到
``OpenMethodOpsServiceProtocol``——发布机会、申请提交、筛选、
录取,指定执行 bot 至 ADMITTED(spec 增量裁决 §9)。

幂等键统一服务端生成(``opportunity_id``/``application_id`` 传
``None``):每次 ``publish_task`` 都是新 Collet,编排无重放路径;
服务端记录首次发布时间,同 ID 同请求形态重放不会重开窗口。

失败不翻译、不吞:任一步抛错原样上穿发布编排(Collet 已持久不
回滚);screen/admit 中途失败留下的 OPEN 机会孤儿行是 P1 接受的
中间态,不做补偿(spec §9)。
"""

from __future__ import annotations

from uuid import uuid4

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenScreenCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    require_designated_admission,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    ApplyForTaskCommand,
    AssigneeAdmissionCommand,
    AssigneeAdmissionReceipt,
    TaskMethodOpsPort,
    TaskOpportunityView,
)


class TaskCommissionOpenMethodAdapter(TaskMethodOpsPort):
    """方法编排缝实现:注入 Open 方法服务 singleton,四步串行驱动。"""

    @inject
    def __init__(
        self, open_ops: OpenMethodOpsServiceProtocol, product_catalog: ProductCatalog
    ) -> None:
        self._product_catalog = product_catalog
        self._open_ops = open_ops

    def admit_designated_assignee(
        self, command: AssigneeAdmissionCommand, context
    ) -> AssigneeAdmissionReceipt:
        profile = self._product_catalog.get(
            command.product_profile_ref.profile_id, command.product_profile_ref.version
        )
        policy = require_designated_admission(profile)
        # The bounded executor implements the declared publisher-mediated strategy.
        # G1 in OpenMethodService still authorizes each operation; this is not a grant.
        if command.accountable_principal_id != context.actor.principal_id:
            raise ContextCollabValidationError(
                "admission accountable principal must be publisher"
            )
        binding_id = command.method_binding_ref
        if binding_id != top_level_binding_id(command.collab_context_id):
            raise ContextCollabValidationError(
                "publication requires the Collet's top-level binding"
            )
        published = self._open_ops.publish_opportunity(
            OpenOpportunityCommand(
                collet_id=command.collab_context_id,
                actor_context=context,
                participation_type=policy.participation_type,
                capacity=policy.capacity,
                reason="task_commission.publish",
            ),
        )
        submitted = self._open_ops.submit_application(
            OpenApplicationCommand(
                opportunity_ref=published.object_id,
                actor_context=context,
                applicant_agent_ref=command.assignee_agent_id,
                reason="task_commission.designated_assignee",
            ),
        )
        self._open_ops.screen(
            command=OpenScreenCommand(
                application_ref=submitted.object_id,
                context=context,
                collect_id=command.collab_context_id,
                reason="task_commission.assignee_screening",
            ),
        )
        decision_id = f"dec_{uuid4().hex[:12]}"
        admitted = self._open_ops.admit(
            command=OpenAdmitCommand(
                application_ref=submitted.object_id,
                context=context,
                collect_id=command.collab_context_id,
                decision_id=decision_id,
                reason="task_commission.assigned_admission",
            ),
        )
        publication = profile.composition.require_policy("Open").publication
        publication.require_dispatch_ready(admitted.state)
        return AssigneeAdmissionReceipt(
            opportunity_id=published.object_id,
            application_id=submitted.object_id,
            decision_id=decision_id,
            state=admitted.state,
        )

    def publish_designated_opportunity(
        self, command: AssigneeAdmissionCommand, context: ActorContext
    ) -> str:
        raise ContextCollabValidationError(
            "v1 adapter does not support Bot self-application"
        )

    def query_opportunities(
        self, profile_ref: VersionedProfileRef, context: ActorContext
    ) -> tuple[TaskOpportunityView, ...]:
        raise ContextCollabValidationError("v1 adapter does not support Bot discovery")

    def apply_for_task(
        self, command: ApplyForTaskCommand, context: ActorContext
    ) -> AssigneeAdmissionReceipt:
        raise ContextCollabValidationError(
            "v1 adapter does not support Bot self-application"
        )

    def review_task_application(
        self, command: ApplyForTaskCommand, application_id: str, context: ActorContext
    ) -> AssigneeAdmissionReceipt:
        raise ContextCollabValidationError(
            "v1 adapter does not support Bot application review"
        )

    def require_admitted(self, collab_context_id: str, context: ActorContext) -> None:
        raise ContextCollabValidationError(
            "v1 adapter cannot establish executor membership"
        )
