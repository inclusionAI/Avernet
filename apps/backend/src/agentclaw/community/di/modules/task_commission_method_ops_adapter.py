"""任务代办方法编排缝适配器:产品窄端口 → L5 Open 方法服务。

产品白名单(乙案)不允许 products 直连 ``method_service_api`` /
``l5_methods.operations.open``;本适配器住 DI 层(不受产品
import 法约束),把产品的 ``TaskMethodOpsPort`` 四步翻译到
``OpenMethodOpsServiceProtocol``——发布机会、申请提交、筛选、
录取,指定执行 bot 至 ADMITTED(spec 增量裁决 §9)。

幂等键统一服务端生成(``opportunity_id``/``application_id`` 传
``None``):每次 ``publish_task`` 都是新 Collet,编排无重放路径;
确定性键 + 重打时间戳反而触发 replay 形状校验(``opens_at`` 参与
机会形态比对)——除非将来引入"对既有 Collet 重跑发布"。

失败不翻译、不吞:任一步抛错原样上穿发布编排(Collet 已持久不
回滚);screen/admit 中途失败留下的 OPEN 机会孤儿行是 P1 接受的
中间态,不做补偿(spec §9)。
"""

from __future__ import annotations

from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    require_designated_admission,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    AssigneeAdmissionCommand,
    AssigneeAdmissionReceipt,
    TaskMethodOpsPort,
)
from injector import inject


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
            command.collab_context_id,
            binding_id,
            OpenOpportunityCommand(
                scope_ref=command.collab_context_id,
                participation_type=policy.participation_type.value,
                opens_at=_now(),
                capacity=policy.capacity,
                reason="task_commission.publish",
            ),
            context,
        )
        submitted = self._open_ops.submit_application(
            command.collab_context_id,
            binding_id,
            published.object_id,
            OpenApplicationCommand(
                applicant_agent_ref=command.assignee_agent_id,
                accountable_principal_ref=(command.accountable_principal_id),
                reason="task_commission.designated_assignee",
            ),
            context,
        )
        self._open_ops.screen(
            command.collab_context_id,
            binding_id,
            submitted.object_id,
            OpenDecisionOpCommand(
                reason="task_commission.assignee_screening",
            ),
            context,
        )
        decision_id = f"dec_{uuid4().hex[:12]}"
        admitted = self._open_ops.admit(
            command.collab_context_id,
            binding_id,
            submitted.object_id,
            OpenAdmitCommand(
                admitted_scope_ref=command.collab_context_id,
                decision_id=decision_id,
                reason="task_commission.assigned_admission",
            ),
            context,
        )
        profile.require_publication().require_dispatch_ready(admitted.state)
        return AssigneeAdmissionReceipt(
            opportunity_id=published.object_id,
            application_id=submitted.object_id,
            decision_id=decision_id,
            state=admitted.state,
        )
