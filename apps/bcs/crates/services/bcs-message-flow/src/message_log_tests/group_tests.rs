use super::*;
use crate::message_log_tests::{capture, decision};

#[test]
fn coverage_legacy_group_logs_keep_session_fallback_and_delivery_failures() {
    use crate::message_log_tests::legacy;
    legacy::isolated(|| {
        let cmd = WebSendCommand {
            caller_execution: None, caller: CallerContext::Public, group_id: "group".into(),
            session_id: Some(String::new()), from_actor_id: "human".into(), from_name: None,
            message: "@send 你好".into(), mentions: vec!["send".into()], attachments: None, thinking: None,
            idempotency_key: None, source_im_message_id: None, channel_sender_identity: None,
            sender_conn_id: None, provider_bypass_headers: Vec::new(), caller_ingress: None, speaking_lease: None,
        };
        log_message_received(&cmd, MessageLogMode::FreeChat);
        log_routing_digest(&cmd, &decision(), MessageLogMode::FreeChat, "explicit_mentions");
        log_bot_deliver_result("group", None, "run", "send", DeliveryType::Send, true, None, None);
        log_bot_deliver_result("group", Some(""), "run", "inject", DeliveryType::Inject, false, Some("offline"), Some("send"));
        let rows = legacy::rows(MSG_LOG_TARGET);
        assert_eq!(rows.len(), 4);
        for row in &rows {
            row.assert_field("log_class", "business_key");
            row.assert_field("session_id", "group");
        }
        rows[0].assert_field("content", "@send 你好");
        rows[1].assert_field("content", "@send 你好");
        rows[1].assert_field("target_count", "2");
        rows[1].assert_field("send_target_count", "1");
        rows[1].assert_field("inject_target_count", "1");
        rows[2].assert_field("delivered", "true");
        rows[3].assert_field("delivered", "false");
        rows[3].assert_field("error", "offline");
        rows[3].assert_field("failure_phase", "send");
        assert_eq!(rows[3].level, tracing::log::Level::Warn);
    });
}

#[test]
fn group_logs_keep_original_text_routing_counts_and_success_or_failure() {
    let cmd = WebSendCommand {
        caller_execution: None, caller: CallerContext::Public, group_id: "group".into(),
        session_id: Some("session".into()), from_actor_id: "human".into(), from_name: Some("Alice".into()),
        message: "@send 你好".into(), mentions: vec!["send".into()], attachments: None, thinking: None,
        idempotency_key: None, source_im_message_id: None, channel_sender_identity: None,
        sender_conn_id: None, provider_bypass_headers: Vec::new(), caller_ingress: None, speaking_lease: None,
    };
    let rows = capture(|| {
        log_message_received(&cmd, MessageLogMode::FreeChat);
        log_routing_digest(&cmd, &decision(), MessageLogMode::FreeChat, "explicit_mentions");
        log_bot_deliver_result("group", Some("session"), "run", "send", DeliveryType::Send, true, None, None);
        log_bot_deliver_result("group", Some("session"), "run", "inject", DeliveryType::Inject, false, Some("offline"), Some("send"));
    });
    assert_eq!(rows.len(), 4);
    for row in &rows {
        assert_eq!(row["fields"]["log_class"], "business_key");
        assert_eq!(row["fields"]["session_id"], "session");
    }
    assert_eq!(rows[0]["fields"]["content"], cmd.message);
    assert_eq!(rows[1]["fields"]["content"], cmd.message);
    assert_eq!(rows[1]["fields"]["target_count"], 2);
    assert_eq!(rows[1]["fields"]["send_target_count"], 1);
    assert_eq!(rows[1]["fields"]["inject_target_count"], 1);
    assert_eq!(rows[2]["fields"]["delivered"], true);
    assert_eq!(rows[2]["fields"]["error"], "");
    assert_eq!(rows[3]["fields"]["delivered"], false);
    assert_eq!(rows[3]["fields"]["error"], "offline");
    assert_eq!(rows[3]["level"], "WARN");
}
