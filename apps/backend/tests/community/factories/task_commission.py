"""Wire requests for real commission ingress tests (no credentials)."""

from datetime import datetime, timezone

from agentclaw.community.core.task.context_collab.l5_methods.method_binding import top_level_binding_id
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import TaskCommissionCreatedView

BOT_ID = "bot_writer_001:u_bot_owner"
OWNER = "u_bot_owner"


def commission_report_request(
    created: TaskCommissionCreatedView, action="start", revision=2, payload=None,
):
    collet = {"object_type": "Collet", "object_id": created.collet_id,
              "version": None, "issuer": "avernet"}
    envelope = {
        "operation_id": "op-1", "layer": "L4", "operation_family": "work",
        "action": action, "mode": "COMMAND", "target_ref": collet,
        "expected_revision": revision, "input_refs": [], "payload": payload or {},
        "context": {
            "request_id": "req-1", "idempotency_key": None,
            "caller": {"identity_type": "AGENT", "identity_id": BOT_ID, "issuer": "avernet"},
            "accountable_principal_ref": {"identity_type": "PRINCIPAL", "identity_id": OWNER,
                                          "issuer": "avernet"},
            "delegation_chain_refs": [], "space_refs": [],
            "collet_ref": {"context_type": "COLLET", "context_id": created.collet_id},
            "method_binding_ref": {"object_type": "MethodBinding",
                                   "object_id": top_level_binding_id(created.collet_id),
                                   "version": None, "issuer": "avernet"},
            "product_profile_ref": {"object_type": "ProductProfile", "object_id": "task_commission",
                                    "version": "1", "issuer": "avernet"},
            "trace_id": "trace-1", "correlation_id": None, "causation_id": None,
            "requested_at": datetime.now(timezone.utc).isoformat(), "deadline": None,
            "protocol_versions": {"accs": "0.2", "a2a": "0.3.0"}, "extensions": {},
        },
    }
    return {"jsonrpc": "2.0", "id": "rpc-1", "method": "message/send", "params": {
        "message": {"kind": "message", "messageId": "msg-1", "role": "user",
                    "parts": [{"kind": "data", "data": envelope}]}}}

