"""L1 Interoperate & Deliver 值对象严格面单测(plan T1;ACCS-001 §3–§9/ACCS-101 §2)。

纪律标尺 = collet.py 同级:必填非空、枚举闭包、未知顶层键拒绝、
round-trip 无损;wire 时间戳一律 ISO 字符串(裁决 E)。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.a2a_wire import (
    AccsAgentCardExtension,
    AgentCard,
    A2aArtifact,
    A2aMessage,
    A2aTask,
    Part,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.bridge import (
    A2ABridgeRecord,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    AccsError,
    ContextRef,
    IdentityRef,
    ObjectRef,
    OperationContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
    AccsOperationResult,
)

ISO = "2026-11-13T08:00:00+00:00"


def _identity(kind: str = "PRINCIPAL", iid: str = "u_1001") -> dict:
    return {"identity_type": kind, "identity_id": iid, "issuer": "avernet"}


def _object_ref(otype: str = "Collet", oid: str = "collet_1") -> dict:
    return {"object_type": otype, "object_id": oid, "version": "3", "issuer": "avernet"}


def _context() -> dict:
    return {
        "request_id": "req_1",
        "idempotency_key": "idem_1",
        "caller": _identity("AGENT", "dispatcher"),
        "accountable_principal_ref": _identity("PRINCIPAL", "u_1001"),
        "delegation_chain_refs": [_object_ref("Delegation", "del_1")],
        "space_refs": [_object_ref("Space", "space_1")],
        "collet_ref": {"context_type": "COLLET", "context_id": "collet_1"},
        "method_binding_ref": None,
        "product_profile_ref": None,
        "trace_id": "trace_1",
        "correlation_id": "corr_1",
        "causation_id": "cause_1",
        "requested_at": ISO,
        "deadline": None,
        "protocol_versions": {"accs": "0.2"},
        "extensions": {},
    }


class TestCommon:
    def test_identity_ref_roundtrip_and_closure(self):
        ref = IdentityRef.from_dict(_identity())
        assert ref.identity_type == "PRINCIPAL"
        assert IdentityRef.from_dict(ref.to_dict()) == ref
        with pytest.raises(ContextCollabValidationError):
            IdentityRef.from_dict(
                {**_identity(), "identity_type": "ROBOT"}
            )
        with pytest.raises(ContextCollabValidationError):
            IdentityRef.from_dict(
                {**_identity(), "issuer": ""}
            )
        with pytest.raises(ContextCollabValidationError):
            IdentityRef.from_dict({**_identity(), "extra": 1})

    def test_object_ref_optional_version_issuer(self):
        ref = ObjectRef.from_dict(
            {"object_type": "Collet", "object_id": "collet_1"}
        )
        assert ref.version is None and ref.issuer is None
        assert ObjectRef.from_dict(ref.to_dict()) == ref

    def test_context_ref_closure_eight(self):
        for kind in (
            "SPACE", "COLLET", "TASK", "ACTIVITY", "METHOD_BINDING",
            "PRODUCT", "IMPROVEMENT_RUN", "EVALUATION",
        ):
            ref = ContextRef.from_dict(
                {"context_type": kind, "context_id": "x"}
            )
            assert ref.context_type == kind
        with pytest.raises(ContextCollabValidationError):
            ContextRef.from_dict({"context_type": "GALAXY", "context_id": "x"})

    def test_operation_context_roundtrip_nested(self):
        ctx = OperationContext.from_dict(_context())
        assert ctx.caller.identity_type == "AGENT"
        assert ctx.accountable_principal_ref.identity_id == "u_1001"
        assert ctx.delegation_chain_refs[0].object_id == "del_1"
        assert ctx.collet_ref.context_type == "COLLET"
        restored = OperationContext.from_dict(ctx.to_dict())
        assert restored == ctx

    def test_operation_context_required_request_and_trace(self):
        base = _context()
        for key in ("request_id", "caller", "trace_id", "requested_at"):
            body = {k: v for k, v in base.items() if k != key}
            with pytest.raises(ContextCollabValidationError):
                OperationContext.from_dict(body)

    def test_accs_error_nine_categories_and_required_trace(self):
        for category in (
            "VALIDATION", "AUTHENTICATION", "AUTHORIZATION", "CONFLICT",
            "NOT_FOUND", "RATE_LIMIT", "DEPENDENCY", "TIMEOUT", "INTERNAL",
        ):
            err = AccsError.from_dict(
                {
                    "code": "E1",
                    "message": "x",
                    "retryable": True,
                    "category": category,
                    "trace_id": "t1",
                    "object_ref": None,
                }
            )
            assert err.category == category
        with pytest.raises(ContextCollabValidationError):
            AccsError.from_dict(
                {
                    "code": "E1",
                    "message": "x",
                    "retryable": False,
                    "category": "MYSTERY",
                    "trace_id": "t1",
                    "object_ref": None,
                }
            )
        with pytest.raises(ContextCollabValidationError):
            AccsError.from_dict(
                {
                    "code": "E1",
                    "message": "x",
                    "retryable": False,
                    "category": "INTERNAL",
                    "object_ref": None,
                }
            )


class TestEnvelope:
    def test_envelope_roundtrip(self):
        body = {
            "operation_id": "op_1",
            "layer": "L4",
            "operation_family": "message",
            "action": "publish",
            "mode": "EVENT",
            "context": _context(),
            "target_ref": _object_ref(),
            "expected_revision": 2,
            "input_refs": [],
            "payload": {"content": "正文"},
        }
        envelope = AccsOperationEnvelope.from_dict(body)
        assert envelope.mode == "EVENT"
        assert envelope.context.caller.identity_id == "dispatcher"
        assert AccsOperationEnvelope.from_dict(envelope.to_dict()) == envelope

    def test_envelope_closures(self):
        base = {
            "operation_id": "op_1",
            "layer": "L4",
            "operation_family": "message",
            "action": "publish",
            "mode": "EVENT",
            "context": _context(),
            "target_ref": None,
            "expected_revision": None,
            "input_refs": [],
            "payload": None,
        }
        with pytest.raises(ContextCollabValidationError):
            AccsOperationEnvelope.from_dict({**base, "layer": "L1"})
        with pytest.raises(ContextCollabValidationError):
            AccsOperationEnvelope.from_dict({**base, "mode": "COMMANDMENT"})

    def test_result_roundtrip_with_error(self):
        body = {
            "operation_ref": _object_ref("Operation", "op_1"),
            "status": "ACCEPTED",
            "result_refs": [_object_ref("A2aMessage", "msg_1")],
            "event_refs": [],
            "error": {
                "code": "E1",
                "message": "x",
                "retryable": False,
                "category": "INTERNAL",
                "trace_id": "t1",
                "object_ref": None,
            },
        }
        result = AccsOperationResult.from_dict(body)
        assert result.status == "ACCEPTED"
        assert result.error.category == "INTERNAL"
        assert AccsOperationResult.from_dict(result.to_dict()) == result
        with pytest.raises(ContextCollabValidationError):
            AccsOperationResult.from_dict({**body, "status": "SENT"})


class TestA2aWire:
    def test_agent_card_with_accs_extension_roundtrip(self):
        card_body = {
            "agent_id": "bot_writer_001",
            "endpoint": "a2a://local/bot_writer_001",
            "skills": ["write-report"],
            "protocol_version": "a2a-mock",
            "accs_extension": {
                "accs_versions": ["0.2"],
                "identity_ref": _identity("AGENT", "bot_writer_001"),
                "accountable_principal_refs": [_identity()],
                "capability_profile_refs": [],
                "supported_method_profiles": [
                    _object_ref("MethodProfile", "Open")
                ],
                "supported_product_profiles": [
                    _object_ref("ProductProfile", "task_commission")
                ],
                "supported_g3_profiles": ["OBSERVABLE"],
                "conformance_evidence_refs": [],
            },
        }
        card = AgentCard.from_dict(card_body)
        assert card.accs_extension.identity_ref.identity_id == "bot_writer_001"
        assert AgentCard.from_dict(card.to_dict()) == card

    def test_g3_profile_closure(self):
        body = {
            "agent_id": "b1",
            "endpoint": "a2a://local/b1",
            "skills": [],
            "protocol_version": "a2a-mock",
            "accs_extension": {
                "accs_versions": ["0.2"],
                "identity_ref": _identity("AGENT", "b1"),
                "accountable_principal_refs": [],
                "capability_profile_refs": [],
                "supported_method_profiles": [],
                "supported_product_profiles": [],
                "supported_g3_profiles": ["MEEK"],
                "conformance_evidence_refs": [],
            },
        }
        with pytest.raises(ContextCollabValidationError):
            AgentCard.from_dict(body)

    def test_part_kinds_and_message_tuple(self):
        message = A2aMessage.from_dict(
            {
                "message_id": "msg_1",
                "role": "agent",
                "parts": [{"part_kind": "ENVELOPE", "body": {"k": "v"}}],
            }
        )
        assert message.parts[0].part_kind == "ENVELOPE"
        with pytest.raises(ContextCollabValidationError):
            Part.from_dict({"part_kind": "VIBRATO", "body": {}})

    def test_task_and_artifact_closures(self):
        for status in ("SUBMITTED", "WORKING", "COMPLETED", "FAILED", "CANCELED"):
            task = A2aTask.from_dict(
                {"task_id": "a2a_t1", "status": status, "result": None}
            )
            assert task.status == status
        with pytest.raises(ContextCollabValidationError):
            A2aTask.from_dict(
                {"task_id": "a2a_t1", "status": "VAPORIZED", "result": None}
            )
        artifact = A2aArtifact.from_dict(
            {
                "artifact_id": "art_1",
                "parts": [{"part_kind": "TEXT", "body": {"text": "hi"}}],
            }
        )
        assert A2aArtifact.from_dict(artifact.to_dict()) == artifact


class TestBridge:
    def test_bridge_record_roundtrip_and_closure(self):
        body = {
            "bridge_id": "br_1",
            "accs_object_ref": _object_ref("Operation", "op_1"),
            "a2a_object_type": "MESSAGE",
            "a2a_object_id": "msg_1",
            "direction": "OUTBOUND",
            "transformation_profile_ref": None,
            "content_digest": "sha256:abc",
            "created_at": ISO,
        }
        record = A2ABridgeRecord.from_dict(body)
        assert A2ABridgeRecord.from_dict(record.to_dict()) == record
        with pytest.raises(ContextCollabValidationError):
            A2ABridgeRecord.from_dict({**body, "a2a_object_type": "TELEGRAM"})
        with pytest.raises(ContextCollabValidationError):
            A2ABridgeRecord.from_dict({**body, "direction": "SIDEWAYS"})


class TestAccsErrorDetails:
    def test_details_optional_field_roundtrip(self):
        """ACCS-001 §9 逐字含 details 可选字段(评审 Important#5)。"""
        body = {
            "code": "E1",
            "message": "x",
            "retryable": False,
            "category": "INTERNAL",
            "trace_id": "t1",
            "object_ref": None,
            "details": {"hint": "boom"},
        }
        err = AccsError.from_dict(body)
        assert err.details == {"hint": "boom"}
        assert AccsError.from_dict(err.to_dict()) == err
