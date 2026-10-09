//! Existing workbench system-chat envelope, shared by immediate and queued delivery.

pub fn build_frontend_system_event_frame(
    group_id: &str,
    content: &str,
    session_id: &str,
    bot_uuid: &str,
    run_id: &str,
    timestamp: u64,
) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat",
        "group_id": group_id,
        "bot_uuid": bot_uuid,
        "payload": {
            "bcs_group_id": group_id,
            "bcs_session_id": session_id,
            "run_id": run_id,
            "state": "final",
            "message": {
                "role": "system",
                "content": [{"type": "text", "text": content}],
                "timestamp": timestamp,
            },
        },
    }).to_string()
}
