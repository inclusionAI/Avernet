"""任务代办方法编排缝适配器:产品窄端口 → L5 Open 方法服务。

产品白名单(乙案)不允许 products 直连 ``method_service_api`` /
``services.open_method_service*``;本适配器住 DI 层(不受产品
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

from injector import inject

from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenOpportunityCommand,
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    AssigneeAdmissionCommand,
    AssigneeAdmissionReceipt,
    TaskMethodOpsPort,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    _now,
    top_level_binding_id,
)


class TaskCommissionOpenMethodAdapter(TaskMethodOpsPort):
    """方法编排缝实现:注入 Open 方法服务 singleton,四步串行驱动。"""

    @inject
    def __init__(self, open_ops: OpenMethodOpsServiceProtocol) -> None:
        self._open_ops = open_ops

    def admit_designated_assignee(
        self, command: AssigneeAdmissionCommand, context
    ) -> AssigneeAdmissionReceipt:
        binding_id = top_level_binding_id(command.collab_context_id)
        published = self._open_ops.publish_opportunity(
            command.collab_context_id,
            binding_id,
            OpenOpportunityCommand(
                scope_ref=command.collab_context_id,
                participation_type="SINGLE_CONTRIBUTION",
                opens_at=_now(),
                capacity=1,
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
                accountable_principal_ref=(
                    command.accountable_principal_id
                ),
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
        return AssigneeAdmissionReceipt(
            opportunity_id=published.object_id,
            application_id=submitted.object_id,
            decision_id=decision_id,
            state=admitted.state,
        )
