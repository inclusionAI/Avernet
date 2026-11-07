use super::*;
mod submission;
mod polling;

    fn queued_submit() -> ChatRunSubmitResponse {
        serde_json::from_value(serde_json::json!({
            "run_id": "run-queued", "bot_uuid": "bot-target", "session_id": "session-queued",
            "status": "pending", "delivery": {
                "delivery_id": "delivery-queued", "message_id": "message-queued",
                "status": "queued", "wait_reason": "lane_busy", "state_version": 1
            }
        })).unwrap()
    }

    fn queued_status(state: &str, delivery: &str, version: u64) -> serde_json::Value {
        serde_json::json!({
            "run_id": "run-queued", "bot_uuid": "bot-target", "from_bot_id": "bot-source",
            "session_id": "session-queued", "state": state, "response": {"content": "answer"},
            "created_at_ms": 1, "updated_at_ms": version, "expires_at_ms": 9999999,
            "version": version, "is_terminal": matches!(state, "completed" | "failed" | "cancelled"),
            "delivery": {"delivery_id": "delivery-queued", "message_id": "message-queued",
                "status": delivery, "state_version": version}
        })
    }
