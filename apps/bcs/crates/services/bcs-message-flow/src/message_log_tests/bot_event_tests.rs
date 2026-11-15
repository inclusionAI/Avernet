use super::*;
use crate::message_log_tests::{capture, decision};

#[test]
fn coverage_legacy_bot_events_select_terminals_and_keep_relay_run_ids_distinct() {
    use crate::message_log_tests::legacy;
    legacy::isolated(|| {
        let mut cmd = BotEventCommand {
            bot_id: "sender".into(), run_id: "parent-run".into(), group_id: "group".into(),
            event_type: "chat.event".into(), event_payload: serde_json::json!({"message": {"content": [{"type": "text", "text": "你好"}]}}),
            state: ChatEventState::Delta, bcs_session_id: None,
        };
        for state in [ChatEventState::Delta, ChatEventState::Final, ChatEventState::Error, ChatEventState::Aborted] {
            cmd.state = state;
            log_incoming_bot_event(&cmd, None);
        }
        log_route_digest(&cmd, &decision(), "你好", &RequestSource::StructuredMetadata);
        log_relay_deliver_result(&cmd, "child-run", "send", DeliveryType::Send, true, None, None, &RequestSource::StructuredMetadata);
        log_relay_deliver_result(&cmd, "child-run", "inject", DeliveryType::Inject, false, Some("offline"), Some("send"), &RequestSource::DefaultPolicy);
        let rows = legacy::rows(MSG_LOG_TARGET);
        assert_eq!(rows.len(), 7);
        assert!(!rows[0].message.contains("log_class="));
        for row in &rows[1..] { row.assert_field("log_class", "business_key"); }
        for row in &rows[..4] {
            row.assert_field("run_id", "parent-run");
            row.assert_field("session_id", "group");
            row.assert_field("content", "你好");
        }
        rows[4].assert_field("send_target_count", "1");
        rows[4].assert_field("inject_target_count", "1");
        for row in &rows[5..] {
            row.assert_field("parent_run_id", "parent-run");
            row.assert_field("run_id", "child-run");
        }
        rows[5].assert_field("delivered", "true");
        rows[6].assert_field("error", "offline");
        rows[6].assert_field("failure_phase", "send");
        assert_eq!(rows[6].level, tracing::log::Level::Warn);
    });
}

#[test]
fn bot_event_route_and_relay_logs_select_terminals_and_preserve_delivery_identity() {
    let mut cmd = BotEventCommand {
        bot_id: "sender".into(), run_id: "parent-run".into(), group_id: "group".into(),
        event_type: "chat.event".into(), event_payload: serde_json::json!({"message": {"content": [{"type": "text", "text": "你好"}]}}),
        state: ChatEventState::Delta, bcs_session_id: Some("session".into()),
    };
    let rows = capture(|| {
        for state in [ChatEventState::Delta, ChatEventState::ToolCallStart, ChatEventState::ToolCallEnd,
            ChatEventState::Final, ChatEventState::Error, ChatEventState::Aborted] {
            cmd.state = state;
            log_incoming_bot_event(&cmd, None);
        }
        log_incoming_bot_event(&cmd, Some("task"));
        log_route_digest(&cmd, &decision(), "你好", &RequestSource::StructuredMetadata);
        log_relay_deliver_result(&cmd, "child-run", "send", DeliveryType::Send, true, None, None, &RequestSource::StructuredMetadata);
        log_relay_deliver_result(&cmd, "child-run", "inject", DeliveryType::Inject, false, Some("offline"), Some("send"), &RequestSource::DefaultPolicy);
    });
    assert_eq!(rows.len(), 10);
    for (i, row) in rows[..6].iter().enumerate() {
        assert_eq!(row["fields"]["log_class"].as_str(), (i >= 3).then_some("business_key"));
        assert_eq!(row["fields"]["content"], "你好");
        assert_eq!(row["fields"]["run_id"], "parent-run");
    }
    assert_eq!(rows[6]["fields"]["task_id"], "task");
    assert_eq!(rows[7]["fields"]["send_target_count"], 1);
    assert_eq!(rows[7]["fields"]["inject_target_count"], 1);
    assert_eq!(rows[8]["fields"]["parent_run_id"], "parent-run");
    assert_eq!(rows[8]["fields"]["run_id"], "child-run");
    assert_eq!(rows[8]["fields"]["error"], "");
    assert_eq!(rows[9]["level"], "WARN");
    assert_eq!(rows[9]["fields"]["error"], "offline");
    assert_eq!(rows[9]["fields"]["failure_phase"], "send");
    for row in &rows[6..] { assert_eq!(row["fields"]["log_class"], "business_key"); }
}
