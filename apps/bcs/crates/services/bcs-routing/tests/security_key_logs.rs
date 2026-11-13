use std::{collections::BTreeMap, sync::{Arc, Mutex}};
use bcs_routing::security::{SecurityCheckResult, SecurityInterceptor};
use bcs_security_gateway_api::{SecurityCheckRequest, SecurityVerdict};
use bcs_security_gateway_local::FakeSecurityGateway;
use tracing::{Event, Subscriber, field::{Field, Visit}, instrument::WithSubscriber};
use tracing_subscriber::{Layer, layer::Context, prelude::*};

#[path = "../../../test-support/legacy_log_capture.rs"]
mod legacy;

#[test]
fn coverage_legacy_security_logs_preserve_denial_policy_and_redact_request_data() {
    legacy::isolated(|| {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            for (verdict, dry_run) in [
                (SecurityVerdict::Allow { task_id: None }, false),
                (SecurityVerdict::Unavailable { reason: "timeout".into() }, false),
                (SecurityVerdict::Deny { code: "denied".into(), message: "blocked".into() }, true),
                (SecurityVerdict::Deny { code: "denied".into(), message: "blocked".into() }, false),
            ] {
                let interceptor = SecurityInterceptor::new(Arc::new(FakeSecurityGateway::new(verdict.clone())), dry_run);
                let result = interceptor.intercept(SecurityCheckRequest {
                    sender_bot_id: "sender".into(), receiver_bot_id: "receiver".into(),
                    sender_agent_code: None, receiver_agent_code: None, agent_token: None,
                    message_content: "private message fixture".into(), message_id: "original-id".into(),
                }).await;
                match (verdict, dry_run, result) {
                    (SecurityVerdict::Allow { .. }, _, SecurityCheckResult::Pass { task_id }) => assert_eq!(task_id, "original-id"),
                    (SecurityVerdict::Unavailable { .. }, _, SecurityCheckResult::Degraded { reason }) => assert_eq!(reason, "timeout"),
                    (SecurityVerdict::Deny { .. }, true, SecurityCheckResult::Degraded { reason }) => assert!(reason.contains("denied")),
                    (SecurityVerdict::Deny { .. }, false, SecurityCheckResult::Block { error_code, message }) => {
                        assert_eq!(error_code, "denied"); assert_eq!(message, "blocked");
                    }
                    other => panic!("unexpected policy outcome: {other:?}"),
                }
            }
        });
        let rows = legacy::rows("bcs_routing::security::interceptor");
        let selected: Vec<_> = rows.iter().filter(|row| row.message.contains("event_code=")).collect();
        assert_eq!(selected.len(), 3, "successful requests remain ordinary: {rows:?}");
        for (row, code) in selected.iter().zip(["security.degraded", "security.dry_run_denied", "security.denied"]) {
            row.assert_field("event_code", code);
            row.assert_field("log_class", "business_key");
            assert_eq!(row.level, tracing::log::Level::Warn);
        }
        assert!(rows.iter().all(|row| !row.message.contains("private message fixture")));
    });
}

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<BTreeMap<String, String>>>>);
impl<S: Subscriber> Layer<S> for Capture {
    fn on_event(&self, event: &Event<'_>, _: Context<'_, S>) {
        struct Fields(BTreeMap<String, String>);
        impl Visit for Fields {
            fn record_str(&mut self, field: &Field, value: &str) {
                self.0.insert(field.name().into(), value.into());
            }
            fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
                self.0.insert(field.name().into(), format!("{value:?}"));
            }
        }
        let mut fields = Fields(BTreeMap::new());
        event.record(&mut fields);
        if fields.0.contains_key("event_code") { self.0.lock().unwrap().push(fields.0); }
    }
}

#[tokio::test]
async fn security_denial_and_degradation_are_selected_without_selecting_allowed_messages() {
    let capture = Capture::default();
    let output = capture.clone();
    async {
        for (verdict, dry_run, expected) in [
            (SecurityVerdict::Allow { task_id: Some("task".into()) }, false, "pass"),
            (SecurityVerdict::Unavailable { reason: "timeout".into() }, false, "degraded"),
            (SecurityVerdict::Deny { code: "denied".into(), message: "blocked".into() }, true, "degraded"),
            (SecurityVerdict::Deny { code: "denied".into(), message: "blocked".into() }, false, "block"),
        ] {
            let interceptor = SecurityInterceptor::new(Arc::new(FakeSecurityGateway::new(verdict)), dry_run);
            let result = interceptor.intercept(SecurityCheckRequest {
                sender_bot_id: "sender".into(), receiver_bot_id: "receiver".into(),
                sender_agent_code: Some("sender-agent".into()), receiver_agent_code: Some("receiver-agent".into()),
                agent_token: None, message_content: "fixture".into(), message_id: "message".into(),
            }).await;
            match (expected, result) {
                ("pass", SecurityCheckResult::Pass { task_id }) => assert_eq!(task_id, "task"),
                ("degraded", SecurityCheckResult::Degraded { reason }) => assert!(!reason.is_empty()),
                ("block", SecurityCheckResult::Block { error_code, message }) => {
                    assert_eq!(error_code, "denied"); assert_eq!(message, "blocked");
                }
                (_, other) => panic!("expected {expected}, got {other:?}"),
            }
        }
    }.with_subscriber(tracing_subscriber::registry().with(capture)).await;
    let rows = output.0.lock().unwrap();
    assert_eq!(rows.len(), 3, "one key event per failure; allow is ordinary");
    for (row, code) in rows.iter().zip(["security.degraded", "security.dry_run_denied", "security.denied"]) {
        assert_eq!(row.get("event_code").map(String::as_str), Some(code));
        assert_eq!(row.get("log_class").map(String::as_str), Some("business_key"));
    }
    assert_eq!(rows[0].get("reason").map(String::as_str), Some("timeout"));
    assert_eq!(rows[1].get("dry_run").map(String::as_str), Some("true"));
    assert_eq!(rows[2].get("dry_run").map(String::as_str), Some("false"));
}
