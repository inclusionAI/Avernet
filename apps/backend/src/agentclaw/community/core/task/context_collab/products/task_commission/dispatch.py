"""任务交办交付缝的 P1 默认实现(日志送达)。

发布成功后系统把任务信息"送达"指定执行 bot——这是 L1/A2A 渡口的 P1
占位形态(ACCS:"A2A Message 送达、Task 成功...都不等于上层 Operation
成功",交付是旁路,不回滚已持久化的协作权威)。P2 接真实通道
(engine adapter / A2A envelope)时经同一端口协议整体替换,发布编排
零改动。失败如实上抛,由调用方处置(发布已成功,不吞)。
"""

from __future__ import annotations

import logging

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.a2a_wire import (
    A2aMessage,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    ContextRef,
    IdentityRef,
    OperationContext,
    ObjectRef,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    DispatchedTaskInfo,
    TaskDispatchPort,
)

_logger = logging.getLogger(__name__)


class LoggingTaskDispatch(TaskDispatchPort):
    """P1 日志型送达:留痕不外发;DI singleton。"""

    def dispatch_task_info(self, info: DispatchedTaskInfo) -> None:
        _logger.info(
            "task commission dispatched: collet=%s assignee=%s title=%s",
            info.collet_id,
            info.assignee_agent_id,
            info.title,
        )


# 本实现的 IdentityRef issuer 常量(ACCS-001 §3:跨信任域必须验证 issuer;
# P1 单实现无外域,P2 签名验收随真实 A2A 交付补)。
LOCAL_ISSUER = "avernet"
# 交办的线级调用者:本地调度 Agent(替用户把任务送达执行 bot)。
_DISPATCHER_AGENT_ID = "avernet-task-commission-dispatcher"


class A2aTaskDispatch(TaskDispatchPort):
    """经 L1 mock 送达任务信息(DI 绑定实现,裁决 B)。

    把交办语义组装为 AccsOperationEnvelope(EVENT:任务已创建的协作事实)
    经 A2aMessage/Part 承载交 L1——真实传输 P2 整体替换
    ``A2aDeliveryPort`` 实现即达,本类零改动。身份链按 ACCS-001 §5
    规则 1:caller=本地 AGENT 调度者,accountable_principal_ref=发起人。
    """

    @inject
    def __init__(self, a2a_delivery: A2aDeliveryPort) -> None:
        self._delivery = a2a_delivery

    def dispatch_task_info(self, info: DispatchedTaskInfo) -> None:
        import uuid as _uuid

        now = _now_iso_()
        envelope = AccsOperationEnvelope.from_dict(
            {
                "operation_id": f"op_{_uuid.uuid4().hex}",
                "layer": "L4",
                "operation_family": "message",
                "action": "publish",
                "mode": "EVENT",
                "context": _operation_context(info, now),
                "target_ref": {
                    "object_type": "Collet",
                    "object_id": info.collet_id,
                    "version": str(info.context_version),
                    "issuer": LOCAL_ISSUER,
                },
                "expected_revision": None,
                "input_refs": [],
                "payload": {
                    "title": info.title,
                    "body": info.body,
                    "assignee_agent_id": info.assignee_agent_id,
                },
            }
        )
        card = self._delivery.resolve_agent_card(agent_id=info.assignee_agent_id)
        message = A2aMessage.from_dict(
            {
                "message_id": f"a2amsg_{_uuid.uuid4().hex}",
                "role": "agent",
                # ENVELOPE part 承载上层操作封装(ACCS-101 §2:Part 可承载
                # AccsOperationEnvelope)。
                "parts": [
                    {
                        "part_kind": "ENVELOPE",
                        "body": envelope.to_dict(),
                    }
                ],
            }
        )
        result = self._delivery.send_message(card, message, envelope)
        # 失败如实上抛:status 语义位(REJECTED/FAILED)与 error 任一出现
        # 即拒——协议严格面允许"无 error 的 REJECTED",不得静默吞。
        if result.status in {"REJECTED", "FAILED"} or result.error is not None:
            detail = (
                f"{result.error.code} ({result.error.category})"
                if result.error is not None
                else "no error attached"
            )
            raise ContextCollabValidationError(
                f"task dispatch rejected: {result.status} — {detail}"
            )


def _now_iso_() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _operation_context(info: DispatchedTaskInfo, now: str) -> dict:
    return OperationContext.from_dict(
        {
            "request_id": f"req_{info.collet_id}",
            "idempotency_key": None,
            "caller": IdentityRef.from_dict(
                {
                    "identity_type": "AGENT",
                    "identity_id": _DISPATCHER_AGENT_ID,
                    "issuer": LOCAL_ISSUER,
                }
            ).to_dict(),
            "accountable_principal_ref": IdentityRef.from_dict(
                {
                    "identity_type": "PRINCIPAL",
                    "identity_id": info.publisher_principal_id,
                    "issuer": LOCAL_ISSUER,
                }
            ).to_dict(),
            "delegation_chain_refs": [],
            "space_refs": [],
            "collet_ref": ContextRef.from_dict(
                {"context_type": "COLLET", "context_id": info.collet_id}
            ).to_dict(),
            "method_binding_ref": ObjectRef.from_dict(
                {
                    "object_type": "MethodBinding",
                    "object_id": info.method_binding_ref,
                    "version": None,
                    "issuer": LOCAL_ISSUER,
                }
            ).to_dict(),
            "product_profile_ref": ObjectRef.from_dict(
                {
                    "object_type": "ProductProfile",
                    "object_id": info.product_profile_ref.profile_id,
                    "version": str(info.product_profile_ref.version),
                    "issuer": LOCAL_ISSUER,
                }
            ).to_dict(),
            "trace_id": f"trace_{info.collet_id}",
            "correlation_id": None,
            "causation_id": None,
            "requested_at": now,
            "deadline": None,
            "protocol_versions": {"accs": "0.2"},
            "extensions": {},
        }
    ).to_dict()
