use super::*;
use std::{io::Write, sync::Mutex};
use serde_json::json;

#[path = "../../../../test-support/legacy_log_capture.rs"]
mod legacy;

#[derive(Clone, Default)]
struct Buffer(Arc<Mutex<Vec<u8>>>);
impl Write for Buffer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().write(bytes)
    }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}

fn capture(action: impl FnOnce()) -> Vec<Value> {
    let buffer = Buffer::default();
    let output = buffer.clone();
    let subscriber = tracing_subscriber::fmt().json().with_ansi(false)
        .with_max_level(tracing::Level::TRACE).with_writer(move || output.clone()).finish();
    tracing::subscriber::with_default(subscriber, action);
    String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap().lines()
        .map(|line| serde_json::from_str(line).unwrap()).collect()
}

fn run() -> StateMachineRun {
    serde_json::from_value(json!({
        "run_id": "machine-run", "definition_id": "definition", "definition_version": 1,
        "group_id": "group", "session_id": "session", "status": "running",
        "input": null, "created_at": 1, "updated_at": 1
    })).unwrap()
}

fn correlation() -> StateMachineDeliveryCorrelation {
    StateMachineDeliveryCorrelation {
        state_machine_run_id: "machine-run".into(), node_id: "node".into(), attempt: 2,
        assignee_bot_id: "assignee".into(), delivery_request_id: "delivery".into(),
        bot_delivery_run_id: Some("provider-run".into()),
    }
}

#[test]
fn coverage_legacy_state_machine_logs_preserve_terminal_and_delivery_identity() {
    legacy::isolated(|| {
        let run = run();
        let group = Group::new("group", "driver", Vec::new());
        let correlation = correlation();
        log_state_machine_node_dispatch(&group, &run, "node", 2, "assignee", "delivery", "你好");
        for delivered in [true, false] {
            log_state_machine_delivery_result(&group, &run, "node", 2, "assignee", "delivery",
                None, delivered, (!delivered).then_some("offline"), (!delivered).then_some("send"));
        }
        for state in [ChatEventState::Delta, ChatEventState::Final, ChatEventState::Error, ChatEventState::Aborted] {
            let cmd = HandleBotTerminalEventCommand {
                bot_id: "assignee".into(), run_id: "provider-run".into(), event_type: "chat.event".into(),
                state, bcs_session_id: Some("session".into()), event_payload: json!({"delta_text": "你好"}),
            };
            log_state_machine_bot_event(&run, &correlation, &cmd, 6);
        }
        log_state_machine_node_result(&run, &correlation, MessageLogStatus::Completed, Some("ok"), None, Some(6));
        log_state_machine_node_result(&run, &correlation, MessageLogStatus::Failed, None, Some("invalid"), None);
        log_state_machine_transition(&run, "node", "ok", &[], MessageLogStatus::Routed);
        log_state_machine_run_complete(&run, 1, 6, "completed");
        log_state_machine_run_failed(&run, "offline", "failed");
        let node: StateMachineNodeRun = serde_json::from_value(json!({
            "run_id": "machine-run", "node_id": "node", "status": "running", "attempt": 2
        })).unwrap();
        log_state_machine_timeout(&run, &node, 100, None, 10);
        let rows = legacy::rows(MSG_LOG_TARGET);
        assert_eq!(rows.len(), 13, "one legacy record per event: {rows:?}");
        for (index, row) in rows.iter().enumerate() {
            row.assert_field("state_machine_run_id", "machine-run");
            row.assert_field("session_id", "session");
            if index == 3 { assert!(!row.message.contains("log_class=")); }
            else { row.assert_field("log_class", "business_key"); }
        }
        rows[0].assert_field("content", "你好");
        rows[1].assert_field("to_bot_id", "assignee");
        rows[1].assert_field("delivered", "true");
        rows[2].assert_field("error", "offline");
        assert_eq!(rows[2].level, tracing::log::Level::Warn);
        rows[4].assert_field("bot_delivery_run_id", "provider-run");
        rows[7].assert_field("run_id", "delivery");
        rows[8].assert_field("error", "invalid");
        rows[9].assert_field("next_node_count", "0");
        rows[12].assert_field("run_id", "machine-run");
        rows[12].assert_field("timeout_deadline_ms", "0");
    });
}

#[test]
fn dispatch_delivery_and_node_result_logs_preserve_identity_and_failure_fields() {
    let run = run();
    let group = Group::new("group", "driver", Vec::new());
    let correlation = correlation();
    let rows = capture(|| {
        log_state_machine_node_dispatch(&group, &run, "node", 2, "assignee", "delivery", "你好");
        log_state_machine_delivery_result(&group, &run, "node", 2, "assignee", "delivery", None, true, None, None);
        log_state_machine_delivery_result(&group, &run, "node", 2, "assignee", "delivery", Some("target"), false, Some("offline"), Some("send"));
        log_state_machine_node_result(&run, &correlation, MessageLogStatus::Completed, Some("ok"), None, Some(6));
        log_state_machine_node_result(&run, &correlation, MessageLogStatus::Failed, None, Some("invalid"), None);
    });
    assert_eq!(rows.len(), 5);
    for row in &rows {
        assert_eq!(row["fields"]["log_class"], "business_key");
        assert_eq!(row["fields"]["session_id"], "session");
        assert_eq!(row["fields"]["group_id"], "group");
        assert_eq!(row["fields"]["state_machine_run_id"], "machine-run");
        assert_eq!(row["fields"]["run_id"], "delivery");
        assert_eq!(row["fields"]["node_id"], "node");
        assert_eq!(row["fields"]["attempt"], 2);
    }
    assert_eq!(rows[0]["fields"]["content"], "你好");
    assert_eq!(rows[1]["fields"]["to_bot_id"], "assignee");
    assert_eq!(rows[1]["fields"]["error"], "");
    assert_eq!(rows[2]["level"], "WARN");
    assert_eq!(rows[2]["fields"]["to_bot_id"], "target");
    assert_eq!(rows[2]["fields"]["error"], "offline");
    assert_eq!(rows[2]["fields"]["failure_phase"], "send");
    assert_eq!(rows[3]["fields"]["outcome"], "ok");
    assert_eq!(rows[3]["fields"]["artifact_len"], 6);
    assert_eq!(rows[4]["level"], "WARN");
    assert_eq!(rows[4]["fields"]["error"], "invalid");
    assert_eq!(rows[4]["fields"]["artifact_len"], 0);
}

#[test]
fn only_terminal_bot_events_are_business_key_logs() {
    let run = run();
    let correlation = correlation();
    let states = [ChatEventState::Delta, ChatEventState::ToolCallStart, ChatEventState::ToolCallEnd,
        ChatEventState::Final, ChatEventState::Error, ChatEventState::Aborted];
    let rows = capture(|| {
        for state in states {
            let command = HandleBotTerminalEventCommand {
                bot_id: "assignee".into(), run_id: "provider-run".into(),
                event_type: "chat.event".into(), state, bcs_session_id: Some("session".into()),
                event_payload: json!({"message": {"content": [{"type": "text", "text": "你好"}]}}),
            };
            log_state_machine_bot_event(&run, &correlation, &command, 6);
        }
    });
    assert_eq!(rows.len(), 6);
    for (index, row) in rows.iter().enumerate() {
        let fields = &row["fields"];
        assert_eq!(fields["log_class"].as_str(), (index >= 3).then_some("business_key"));
        assert_eq!(fields["bot_delivery_run_id"], "provider-run");
        assert_eq!(fields["state_machine_run_id"], "machine-run");
        assert_eq!(fields["delivery_request_id"], "delivery");
        assert_eq!(fields["content"], "你好");
        assert_eq!(fields["text_len"], 6);
    }
    assert_eq!(rows[3]["fields"]["chat_event_state"], "final");
    assert_eq!(rows[4]["fields"]["chat_event_state"], "error");
    assert_eq!(rows[5]["fields"]["chat_event_state"], "aborted");
}

#[test]
fn transitions_completion_failure_and_timeout_logs_keep_run_and_delivery_ids_distinct() {
    let run = run();
    let mut node: StateMachineNodeRun = serde_json::from_value(json!({
        "run_id": "machine-run", "node_id": "node", "status": "running", "attempt": 2
    })).unwrap();
    let rows = capture(|| {
        log_state_machine_transition(&run, "node", "ok", &["next".into()], MessageLogStatus::Routed);
        log_state_machine_run_complete(&run, 2, 6, "completed");
        log_state_machine_run_failed(&run, "offline", "failed");
        log_state_machine_timeout(&run, &node, 100, None, 10);
        node.delivery_request_id = Some("delivery".into());
        node.bot_delivery_run_id = Some("provider-run".into());
        node.assignee_bot_id = Some("assignee".into());
        log_state_machine_timeout(&run, &node, 100, Some(200), 10);
    });
    assert_eq!(rows.len(), 5);
    for row in &rows {
        assert_eq!(row["fields"]["log_class"], "business_key");
        assert_eq!(row["fields"]["state_machine_run_id"], "machine-run");
    }
    assert_eq!(rows[0]["fields"]["next_node_ids"], "[\"next\"]");
    assert_eq!(rows[0]["fields"]["next_node_count"], 1);
    assert_eq!(rows[1]["fields"]["message"], "run_complete");
    assert_eq!(rows[1]["fields"]["node_count"], 2);
    assert_eq!(rows[2]["level"], "WARN");
    assert_eq!(rows[2]["fields"]["error"], "offline");
    assert_eq!(rows[3]["fields"]["run_id"], "machine-run");
    assert_eq!(rows[3]["fields"]["timeout_deadline_ms"], 0);
    assert_eq!(rows[3]["fields"]["bot_id"], "");
    assert_eq!(rows[4]["fields"]["run_id"], "delivery");
    assert_eq!(rows[4]["fields"]["bot_delivery_run_id"], "provider-run");
    assert_eq!(rows[4]["fields"]["timeout_deadline_ms"], 200);
    assert_eq!(rows[4]["fields"]["timeout_grace_ms"], 10);
}
