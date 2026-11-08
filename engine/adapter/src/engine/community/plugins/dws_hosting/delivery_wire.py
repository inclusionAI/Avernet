"""Conservative projection of DWS v1.0.62 versioned send receipts/status."""

from engine.community.kernel.dws_connection import DwsError
from engine.community.kernel.dws_delivery import DeliveryReceipt
from engine.community.plugins.dws_hosting import wire


def _positive(data: dict, depth=0) -> bool:
    if depth > 8:
        return False
    if (
        ("success" in data and data["success"] is not True)
        or ("ok" in data and data["ok"] is not True)
        or data.get("error")
    ):
        return False
    if "status" in data and data["status"] != "SUCCESS":
        return False
    for key in ("result", "data", "response"):
        nested = data.get(key)
        if isinstance(nested, dict) and not _positive(nested, depth + 1):
            return False
    return True


def receipt(raw: str, *, task_id: str = "") -> DeliveryReceipt:
    data = wire.payload(raw)
    if task_id:
        projected = data
        contract = "im.message-send-status.v1"
    else:
        if (
            data.get("ok") is not True
            or data.get("identity") != "user"
            or data.get("tool") != "send_personal_message"
        ):
            raise DwsError("invalid_send_result")
        projected = data.get("sendReceipt")
        contract = "im.message-send-receipt.v1"
    if not isinstance(projected, dict) or projected.get("contractVersion") != contract:
        raise DwsError("invalid_send_receipt")
    observed_task = projected.get("openTaskId", "")
    if not isinstance(observed_task, str) or (task_id and observed_task != task_id):
        raise DwsError("send_task_mismatch")
    if not _positive(data):
        return DeliveryReceipt(status="unknown", task_id=observed_task)
    ref = projected.get("messageRef", {})
    if not isinstance(ref, dict):
        raise DwsError("invalid_message_reference")
    if projected.get("readyForMessageActions") is True:
        return DeliveryReceipt(
            status="sent",
            task_id=observed_task,
            message_id=wire.identifier(ref.get("openMessageId")),
            conversation_id=wire.identifier(ref.get("openConversationId")),
        )
    if observed_task:
        return DeliveryReceipt(status="accepted", task_id=observed_task)
    return DeliveryReceipt(status="unknown")
