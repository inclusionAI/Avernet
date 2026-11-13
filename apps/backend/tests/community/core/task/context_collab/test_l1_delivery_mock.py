"""L1 ``A2aDeliveryPort`` + Mock 实现单测(plan T1;ACCS-101 §3/§10)。

覆盖(Review Focus 逐条):
- 五族端口行为:发现/发送/任务生命周期/交付/协商声明;
- **重试幂等**(一致性 §10-4):同 operation_id 重发 → 同答、桥不增;
- 桥接簿记(§10-5):每次映射逐条 OUTBOUND 记录;
- 状态不混线(§10-3):回执是 AccsOperationResult,上层语义状态位。
"""

from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.a2a_wire import (
    A2aArtifact,
    A2aMessage,
    A2aTask,
    Part,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    MockA2aDelivery,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
)
from tests.community.core.task.context_collab.test_l1_wire_objects import (
    _context,
)

ISO = "2026-11-13T08:00:00+00:00"


def _card(agent_id: str = "bot_writer_001"):
    return MockA2aDelivery().resolve_agent_card(agent_id=agent_id)


def _envelope(op: str = "op_send", mode: str = "EVENT") -> AccsOperationEnvelope:
    return AccsOperationEnvelope.from_dict(
        {
            "operation_id": op,
            "layer": "L4",
            "operation_family": "message",
            "action": "publish",
            "mode": mode,
            "context": _context(),
            "target_ref": None,
            "expected_revision": None,
            "input_refs": [],
            "payload": {"content": "hello"},
        }
    )


def _message(mid: str = "msg_1") -> A2aMessage:
    return A2aMessage.from_dict(
        {
            "message_id": mid,
            "role": "agent",
            "parts": [{"part_kind": "ENVELOPE", "body": {}}],
        }
    )


class TestDeliveryPortFamilies:
    def test_resolve_agent_card_default_and_injected(self):
        delivery = MockA2aDelivery()
        card = delivery.resolve_agent_card(agent_id="bot_writer_001")
        assert card.agent_id == "bot_writer_001"
        assert card.endpoint == "a2a://mock/bot_writer_001"

    def test_send_message_accepts_and_bridges(self):
        delivery = MockA2aDelivery()
        result = delivery.send_message(
            _card(), _message(), _envelope()
        )
        assert result.status == "ACCEPTED"
        assert result.operation_ref.object_id == "op_send"
        assert result.result_refs[0].object_type == "A2aMessage"
        assert result.result_refs[0].object_id == "msg_1"
        bridges = delivery.bridge_records()
        assert len(bridges) == 1
        assert bridges[0].direction == "OUTBOUND"
        assert bridges[0].a2a_object_type == "MESSAGE"
        assert bridges[0].a2a_object_id == "msg_1"
        assert bridges[0].accs_object_ref.object_type == "Operation"

    def test_task_lifecycle_mock_receipts(self):
        delivery = MockA2aDelivery()
        task = A2aTask.from_dict(
            {"task_id": "a2a_t1", "status": "SUBMITTED", "result": None}
        )
        result = delivery.start_task(_card(), task, _envelope("op_t1"))
        assert result.status == "ACCEPTED"
        assert result.result_refs[0].object_type == "A2aTask"

        # 只读查询:不桥、回线级状态快照。
        before = len(delivery.bridge_records())
        queried = delivery.query_task(_card(), "a2a_t1", _envelope("op_q", "QUERY"))
        assert queried.status == "SUCCEEDED"
        assert len(delivery.bridge_records()) == before

        cancelled = delivery.cancel_task(_card(), "a2a_t1", _envelope("op_c"))
        assert cancelled.status == "ACCEPTED"
        kinds = [b.a2a_object_type for b in delivery.bridge_records()]
        assert kinds.count("TASK") == 2  # start + cancel 各一条

    def test_deliver_artifact_bridges(self):
        delivery = MockA2aDelivery()
        artifact = A2aArtifact.from_dict(
            {
                "artifact_id": "art_1",
                "parts": [{"part_kind": "TEXT", "body": {"text": "报告"}}],
            }
        )
        result = delivery.deliver_artifact(_card(), artifact, _envelope("op_a"))
        assert result.status == "ACCEPTED"
        assert result.result_refs[0].object_type == "A2aArtifact"
        assert delivery.bridge_records()[-1].a2a_object_type == "ARTIFACT"

    def test_declare_protocol(self):
        declaration = MockA2aDelivery().declare_protocol()
        assert declaration["accs"] == "0.2"
        assert "a2a" in declaration


class TestIdempotencyAndAudit:
    def test_send_is_idempotent_per_operation(self):
        """一致性 §10-4:同 operation_id 重发同答,桥不增、不重复副作用。"""
        delivery = MockA2aDelivery()
        envelope = _envelope("op_once")

        first = delivery.send_message(_card(), _message(), envelope)
        second = delivery.send_message(_card(), _message(), envelope)

        assert second == first
        assert len(delivery.bridge_records()) == 1

    def test_bridge_records_returns_snapshot_tuple(self):
        delivery = MockA2aDelivery()
        delivery.send_message(_card(), _message(), _envelope())
        snapshot = delivery.bridge_records()
        assert isinstance(snapshot, tuple)
        delivery.send_message(_card(), _message("msg_2"), _envelope("op_2"))
        assert len(snapshot) == 1  # 快照不随后续写入变化


class TestPayloadlessEnvelope:
    def test_payloadless_envelope_write_path_survives(self):
        """payload 可选(ACCS-001 §6);None 是合法输入,写路径不得崩
        (评审 Critical#1)。"""
        delivery = MockA2aDelivery()
        envelope = _envelope()  # 默认 payload {content}
        stripped = AccsOperationEnvelope.from_dict(
            {**envelope.to_dict(), "payload": None}
        )
        result = delivery.send_message(_card(), _message("msg_pl"), stripped)
        assert result.status == "ACCEPTED"
        assert delivery.bridge_records()[-1].content_digest is None
