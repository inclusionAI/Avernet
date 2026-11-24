use super::*;
use std::sync::{Arc, Mutex};
use tracing::{Event, Subscriber, instrument::WithSubscriber};
use tracing_subscriber::{Layer, layer::Context, prelude::*};

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<Value>>>);
impl<S: Subscriber> Layer<S> for Capture {
    fn on_event(&self, event: &Event<'_>, _: Context<'_, S>) {
        if event.metadata().target() != TARGET { return; }
        struct Fields(serde_json::Map<String, Value>);
        impl tracing::field::Visit for Fields {
            fn record_str(&mut self, field: &tracing::field::Field, value: &str) { self.0.insert(field.name().into(), value.into()); }
            fn record_bool(&mut self, field: &tracing::field::Field, value: bool) { self.0.insert(field.name().into(), value.into()); }
            fn record_u64(&mut self, field: &tracing::field::Field, value: u64) { self.0.insert(field.name().into(), value.into()); }
            fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) { self.record_str(field, &format!("{value:?}")); }
        }
        assert_eq!(*event.metadata().level(), tracing::Level::WARN);
        let mut fields = Fields(Default::default()); event.record(&mut fields);
        self.0.lock().unwrap().push(fields.0.into());
    }
}

#[test]
fn credential_redaction_preserves_card_fields_but_never_reveals_secrets() {
    let body = br#"{"provider_bot_ref":"demo","agent_card":{"name":"Agent","nested":[{"Authorization":"Bearer tttttttttttt-copy"}],"api_key":"tttt","copy":"tttttttttttt-copy"},"provider_admin_token":"tttt"}"#;
    let (text, truncated, redacted) = payload(body, "tttt");
    assert!(redacted); assert!(!truncated);
    assert!(!text.contains("tttttttttttt-copy"));
    let json: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(json["agent_card"]["name"], "Agent");
    assert_eq!(json["provider_bot_ref"], "demo");
    assert_eq!(token_prefix("abcdefgh"), "abcd****");
    for token in ["", "a", "abcd", "你好世界"] { assert_eq!(token_prefix(token), "****"); }
}

#[test]
fn invalid_json_is_omitted_instead_of_logging_unredactable_credential_bytes() {
    let (text, truncated, redacted) = payload(br#"{"token":"ssss", broken"#, "ssss");
    assert!(!redacted); assert!(!truncated); assert!(!text.contains("ssss"));
    assert!(text.contains("invalid JSON"));
}

#[test]
fn large_utf8_payload_is_bounded_after_redaction() {
    let input = serde_json::json!({"agent_card":{"description":"文".repeat(MAX_PAYLOAD_BYTES),"token":"ssss"}}).to_string();
    let (text, truncated, redacted) = payload(input.as_bytes(), "admin-token");
    assert!(truncated && redacted); assert!(text.len() <= MAX_PAYLOAD_BYTES);
    assert!(!text.contains("abcd"));
}

#[tokio::test]
async fn request_failure_has_exact_details_trace_ids_and_one_safe_original_payload() {
    let capture = Capture::default(); let output = capture.clone();
    let context = WarningContext::new("request-one".into(), "POST /agents".into(), "ssssss".into(),
        br#"{"agent_card":{"name":" ","unknown":{"keep":42}},"token":"ssss-copy"}"#.to_vec());
    let ids = bcs_tracing_api::LogCorrelation::new("sofa_trace_id", "trace-one", "sofa_rpc_id", "0.3").unwrap();
    with_warning_context(context, async {
        bcs_tracing_api::with_log_correlation(Some(ids), || {
            warn_current("INVALID_CARD", "agent_card.name", "INVALID_NAME");
            warn_current("fallback", "", "must not duplicate");
        });
    }).with_subscriber(tracing_subscriber::registry().with(capture)).await;
    let rows = output.0.lock().unwrap(); assert_eq!(rows.len(), 1);
    let row = &rows[0];
    for (field, value) in [("request_id","request-one"),("trace_id","trace-one"),("rpc_id","0.3"),
        ("error_code","INVALID_CARD"),("field","agent_card.name"),("reason","INVALID_NAME"),
        ("provider_token_prefix","ssss****"),("log_class","business_key")] { assert_eq!(row[field], value); }
    let body: Value = serde_json::from_str(row["request_payload"].as_str().unwrap()).unwrap();
    assert_eq!(body["agent_card"]["unknown"]["keep"], 42);
    assert_eq!(body["agent_card"]["name"], " ");
    assert_eq!(row["payload_truncated"], false);
    let all = row.to_string(); assert!(!all.contains("ssss-copy")); assert!(!all.contains("ssssss"));
}

#[tokio::test]
async fn successes_are_quiet_and_concurrent_request_scopes_do_not_mix_payloads() {
    let capture = Capture::default(); let output = capture.clone();
    async {
        with_warning_context(WarningContext::new("success".into(), "register".into(), "".into(), b"{}".to_vec()), async {}).await;
        let first = with_warning_context(WarningContext::new("first".into(), "register".into(), "".into(), br#"{"name":"first"}"#.to_vec()), async {
            tokio::task::yield_now().await; warn_current("INVALID_CARD", "name", "FIRST");
        });
        let second = with_warning_context(WarningContext::new("second".into(), "register".into(), "".into(), br#"{"name":"second"}"#.to_vec()), async {
            warn_current("INVALID_CARD", "name", "SECOND");
        });
        tokio::join!(first, second);
    }.with_subscriber(tracing_subscriber::registry().with(capture)).await;
    let rows = output.0.lock().unwrap(); assert_eq!(rows.len(), 2);
    for row in rows.iter() {
        let body: Value = serde_json::from_str(row["request_payload"].as_str().unwrap()).unwrap();
        assert_eq!(body["name"], row["request_id"]);
    }
}

#[test]
fn duplicate_keys_are_kept_and_string_line_breaks_are_json_escaped() {
    let (text, truncated, redacted) = payload(br#"{"name":"first","name":"second","token":"one","token":"two","line":"a\nb"}"#, "admin-token");
    assert!(redacted); assert!(!truncated);
    assert_eq!(text.matches("\"name\"").count(), 2);
    assert_eq!(text.matches("<redacted>").count(), 2);
    assert!(!text.contains('\n'));
    let value: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(value["line"], "a\nb");
}
