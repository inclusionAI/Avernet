"""L1 交付端口与 P1 Mock 实现(ACCS-101 §3 五操作族/§10 一致性)。

``A2aDeliveryPort`` 是 A2A 互操作能力的**域侧端口**:发现/读取 Agent
Card、发送 Message、发起/查询/取消 Task、交付 Artifact、协商声明。
真实传输(HTTP 端点、Agent Card 发现、传输鉴权)P2 由交付侧
(BCS / engine adapter)经同一端口整体接管——本模块零 socket、零路由。

Mock 语义(spec §4):
- 每次写路径(send/start/cancel/deliver)产出 ``A2ABridgeRecord``
  (OUTBOUND、载体类型按对象),返回上层语义回执 ``AccsOperationResult``
  (status=ACCEPTED;A2A 终态不混线,§10-3);
- **operation_id 记忆化**(§10-4):同操作重发→首答、桥不增、
  不重复副作用;
- ``query_task`` 只读不桥;``bridge_records()`` 返回快照元组(审计面)。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.a2a_wire import (
    A2aArtifact,
    A2aMessage,
    A2aTask,
    AgentCard,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.bridge import (
    A2ABridgeRecord,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    ObjectRef,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
    AccsOperationResult,
)


@runtime_checkable
class A2aDeliveryPort(Protocol):
    """L1 交付端口:ACCS-101 §3 五操作族的域侧合同(P1 由 mock 实现)。"""

    def resolve_agent_card(self, *, agent_id: str) -> AgentCard: ...
    def send_message(
        self, card: AgentCard, message: A2aMessage,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult: ...
    def start_task(
        self, card: AgentCard, task: A2aTask,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult: ...
    def query_task(
        self, card: AgentCard, task_id: str,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult: ...
    def cancel_task(
        self, card: AgentCard, task_id: str,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult: ...
    def deliver_artifact(
        self, card: AgentCard, artifact: A2aArtifact,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult: ...
    def declare_protocol(self) -> dict[str, str]: ...
    def bridge_records(self) -> tuple[A2ABridgeRecord, ...]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_digest(payload) -> str | None:
    """payload 可选(ACCS-001 §6):``None`` 是合法输入,digest 亦为 None
    (桥记录的可空字段),不得以空串喂给严格解码桥。"""
    if payload is None:
        return None
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


class MockA2aDelivery(A2aDeliveryPort):
    """P1 全 mock 交付:零 IO、确定性回执、桥接簿记、operation_id 幂等。

    内置默认卡注册表(按 agent_id 惰性生成,测试可经构造注入);
    真实 A2A 到位后经 ``A2aDeliveryPort`` 协议整体替换,调用方零改动。
    """

    def __init__(
        self, cards: dict[str, AgentCard] | None = None
    ) -> None:
        self._cards: dict[str, AgentCard] = dict(cards or {})
        self._bridges: list[A2ABridgeRecord] = []
        # operation_id → (result, 已桥旗) 记忆化表(§10-4)。
        self._memo: dict[str, AccsOperationResult] = {}
        self._tasks: dict[str, str] = {}

    # ------------------------------------------------------------ ① 发现

    def resolve_agent_card(self, *, agent_id: str) -> AgentCard:
        card = self._cards.get(agent_id)
        if card is not None:
            return card
        card = AgentCard.from_dict(
            {
                "agent_id": agent_id,
                "endpoint": f"a2a://mock/{agent_id}",
                "skills": [],
                "protocol_version": "a2a-mock",
                "accs_extension": None,
            }
        )
        self._cards[agent_id] = card
        return card

    # --------------------------------------------------- ②④ 写路径(桥+回执)

    def send_message(
        self, card: AgentCard, message: A2aMessage,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult:
        memo = self._memo.get(envelope.operation_id)
        if memo is not None:
            return memo
        result = self._bridge_and_accept(
            envelope=envelope,
            a2a_object_type="MESSAGE",
            a2a_object_id=message.message_id,
        )
        self._memo[envelope.operation_id] = result
        return result

    def start_task(
        self, card: AgentCard, task: A2aTask,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult:
        memo = self._memo.get(envelope.operation_id)
        if memo is not None:
            return memo
        self._tasks[task.task_id] = task.status
        result = self._bridge_and_accept(
            envelope=envelope,
            a2a_object_type="TASK",
            a2a_object_id=task.task_id,
        )
        self._memo[envelope.operation_id] = result
        return result

    def cancel_task(
        self, card: AgentCard, task_id: str,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult:
        memo = self._memo.get(envelope.operation_id)
        if memo is not None:
            return memo
        if task_id in self._tasks:
            self._tasks[task_id] = "CANCELED"
        result = self._bridge_and_accept(
            envelope=envelope,
            a2a_object_type="TASK",
            a2a_object_id=task_id,
        )
        self._memo[envelope.operation_id] = result
        return result

    def deliver_artifact(
        self, card: AgentCard, artifact: A2aArtifact,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult:
        memo = self._memo.get(envelope.operation_id)
        if memo is not None:
            return memo
        result = self._bridge_and_accept(
            envelope=envelope,
            a2a_object_type="ARTIFACT",
            a2a_object_id=artifact.artifact_id,
        )
        self._memo[envelope.operation_id] = result
        return result

    # ------------------------------------------------------------ ③ 只读

    def query_task(
        self, card: AgentCard, task_id: str,
        envelope: AccsOperationEnvelope,
    ) -> AccsOperationResult:
        if task_id not in self._tasks:
            raise ContextCollabNotFoundError(
                f"a2a task {task_id!r} not found in mock delivery"
            )
        return AccsOperationResult.from_dict(
            {
                "operation_ref": {
                    "object_type": "Operation",
                    "object_id": envelope.operation_id,
                    "version": None,
                    "issuer": None,
                },
                "status": "SUCCEEDED",
                "result_refs": [
                    {
                        "object_type": "A2aTask",
                        "object_id": task_id,
                        "version": None,
                        "issuer": None,
                    }
                ],
                "event_refs": [],
                "error": None,
            }
        )

    # ------------------------------------------------------ ⑤ 协商(声明式)

    def declare_protocol(self) -> dict[str, str]:
        return {
            "accs": "0.2",
            "a2a": "a2a-mock",
            "extension": "accs-agent-card-extension",
        }

    # ------------------------------------------------------------ 审计面

    def bridge_records(self) -> tuple[A2ABridgeRecord, ...]:
        """桥接簿记快照:每次上层操作→A2A 载体映射的逐条证据(§10-5)。"""
        return tuple(self._bridges)

    # ------------------------------------------------------------- 私有

    def _bridge_and_accept(
        self, *, envelope: AccsOperationEnvelope,
        a2a_object_type: str, a2a_object_id: str,
    ) -> AccsOperationResult:
        self._bridges.append(
            A2ABridgeRecord.from_dict(
                {
                    "bridge_id": "bridge_" + uuid4().hex,
                    "accs_object_ref": {
                        "object_type": "Operation",
                        "object_id": envelope.operation_id,
                        "version": None,
                        "issuer": None,
                    },
                    "a2a_object_type": a2a_object_type,
                    "a2a_object_id": a2a_object_id,
                    "direction": "OUTBOUND",
                    "transformation_profile_ref": None,
                    "content_digest": _content_digest(envelope.payload),
                    "created_at": _now_iso(),
                }
            )
        )
        return AccsOperationResult.from_dict(
            {
                "operation_ref": {
                    "object_type": "Operation",
                    "object_id": envelope.operation_id,
                    "version": None,
                    "issuer": None,
                },
                "status": "ACCEPTED",
                "result_refs": [
                    {
                        "object_type": f"A2a{a2a_object_type.title()}",
                        "object_id": a2a_object_id,
                        "version": None,
                        "issuer": None,
                    }
                ],
                "event_refs": [],
                "error": None,
            }
        )
