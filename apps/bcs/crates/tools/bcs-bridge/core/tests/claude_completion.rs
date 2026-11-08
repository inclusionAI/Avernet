//! The real driver against a deterministic native SDK protocol peer.
use std::sync::Arc;
use bcs_bridge_core::engine::{claude_code::{ClaudeCode, ClaudeCodeFactory}, Engine, EngineFactory, SessionObserver, TurnRequest};
use tokio_util::sync::CancellationToken;

struct Observer;
#[async_trait::async_trait]
impl SessionObserver for Observer {
    async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
}

async fn run_case(case: &str, mode: &str) -> Result<bcs_bridge_core::engine::TurnOutcome, String> {
    run_case_traced(case, Some(mode), None).await
}
async fn run_case_traced(case: &str, mode: Option<&str>, trace: Option<bcs_bridge_core::engine::trace::TraceContext>) -> Result<bcs_bridge_core::engine::TurnOutcome, String> {
    let script = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/mock_cc_hook_idle.py");
    let factory = ClaudeCodeFactory::custom("fixture", "python3", vec![script.into(), case.into()]);
    let mut options = toml::Table::new();
    if let Some(mode) = mode { options.insert("completion_mode".into(), mode.into()); }
    let engine = factory.build("python3".into(), &options)?;
    run_engine_case(case, engine, trace).await
}
async fn run_engine_case(case: &str, engine: Arc<dyn Engine>, trace: Option<bcs_bridge_core::engine::trace::TraceContext>) -> Result<bcs_bridge_core::engine::TurnOutcome, String> {
    let cwd = tempfile::tempdir().unwrap();
    let mut req = TurnRequest::new("run-completion", "test", cwd.path(), Arc::new(Observer));
    req.trace = trace;
    req.idle_timeout = Some(std::time::Duration::from_secs(2));
    let (tx, mut rx) = tokio::sync::mpsc::channel(32);
    let drain = tokio::spawn(async move { while rx.recv().await.is_some() {} });
    let abort = CancellationToken::new();
    if case == "nonreading_callback" {
        let token = abort.clone();
        tokio::spawn(async move { tokio::time::sleep(std::time::Duration::from_millis(200)).await; token.cancel(); });
    }
    let result = tokio::time::timeout(std::time::Duration::from_secs(5),
        engine.run_turn(req, tx, abort)).await
        .expect("driver must not hang").map_err(|e| e.to_string());
    drain.await.unwrap();
    result
}

#[tokio::test]
async fn omitted_completion_mode_uses_hook_idle() {
    let result = run_case_traced("midturn", None, None).await.unwrap();
    assert_eq!(result.final_text.as_deref(), Some("DONE"));
}

#[tokio::test]
async fn default_mode_native_auth_failure_does_not_wait_for_completion_hooks() {
    let error = run_case_traced("authentication_error", None, None).await
        .expect_err("native authentication failure must terminate without Stop/idle");
    assert_eq!(error,
        "engine exited: upstream authentication_error (HTTP 401): model provider authentication failed; check credentials");
}

#[tokio::test]
async fn direct_constructor_uses_hook_idle() {
    let script = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/mock_cc_hook_idle.py");
    let engine = Arc::new(ClaudeCode::new("python3".into(), vec![script.into(), "alive".into()]));
    let result = run_engine_case("alive", engine, None).await.unwrap();
    assert_eq!(result.final_text.as_deref(), Some("DONE"));
}

#[tokio::test]
async fn default_mode_never_falls_back_to_legacy() {
    for (case, expected) in [("missing_hook", "completion_evidence_unavailable"),
        ("unknown_version", "completion_capability_unavailable"),
        ("observe_init_failure", "completion_capability_unavailable")] {
        let error = run_case_traced(case, None, None).await.expect_err(case);
        assert!(error.contains(expected), "{case}: {error}");
    }
}

#[tokio::test]
async fn explicit_legacy_skips_hook_initialization() {
    assert_eq!(run_case("legacy", "legacy").await.unwrap().final_text.as_deref(), Some("DONE"));
}

#[tokio::test]
async fn completed_agents_without_delivery_frames_finalize() {
    let result = run_case("midturn", "hook_idle").await.expect("completed work must finalize");
    assert_eq!(result.final_text.as_deref(), Some("DONE"));
}

#[tokio::test]
async fn idle_with_live_background_waits_for_a_fresh_empty_stop() {
    let result = run_case("alive", "hook_idle").await.unwrap();
    assert_eq!(result.final_text.as_deref(), Some("DONE"));
}

#[tokio::test]
async fn queued_notification_discards_the_intermediate_result() {
    let result = run_case("queued", "hook_idle").await.unwrap();
    assert_eq!(result.final_text.as_deref(), Some("DONE"));
}

#[tokio::test]
async fn blocked_stop_requires_a_new_snapshot_after_continuation() {
    let result = run_case("blocked", "hook_idle").await.unwrap();
    assert_eq!(result.final_text.as_deref(), Some("DONE"));
}

#[tokio::test]
async fn unavailable_or_conflicting_evidence_never_returns_success() {
    for case in ["missing_hook", "missing_tasks", "wrong_session", "wrong_callback", "late_task", "unknown_version", "duplicate_hook", "no_idle", "null_tasks", "unknown_task", "scheduled", "too_many_tasks", "old_idle", "late_hook", "duplicate_result", "unbounded_line", "replayed_idle"] {
        let error = run_case(case, "hook_idle").await.expect_err(case);
        assert!((error.contains("completion_evidence_") || error.contains("completion_capability_")), "{case}: {error}");
        assert!(!error.contains("PRIVATE"), "untrusted hook content leaked: {error}");
    }
}

#[tokio::test]
async fn empty_final_text_is_still_a_success() {
    assert_eq!(run_case("empty", "hook_idle").await.unwrap().final_text.as_deref(), Some(""));
}

#[tokio::test]
async fn observe_preserves_the_legacy_completion_point() {
    assert_eq!(run_case("observe", "observe").await.unwrap().final_text.as_deref(), Some("INTERMEDIATE"));
}

#[test]
fn completion_configuration_is_optional_and_validated() {
    let factory = ClaudeCodeFactory::default();
    assert!(factory.build("unused".into(), &toml::Table::new()).is_ok());
    for mode in ["legacy", "observe", "hook_idle"] {
        let mut options = toml::Table::new(); options.insert("completion_mode".into(), mode.into());
        assert!(factory.build("unused".into(), &options).is_ok());
    }
    assert!(factory.build("unused".into(), &toml::toml! { unknown = true }).is_err());
    for value in [toml::Value::Boolean(true), toml::Value::String("auto".into())] {
        let mut options = toml::Table::new(); options.insert("completion_mode".into(), value);
        assert!(factory.build("unused".into(), &options).is_err());
    }
}

#[tokio::test]
async fn pre_canceled_turn_cannot_report_success() {
    let factory = ClaudeCodeFactory::default();
    let mut options = toml::Table::new(); options.insert("completion_mode".into(), "hook_idle".into());
    let engine = factory.build("python3".into(), &options).unwrap();
    let cancel = CancellationToken::new(); cancel.cancel();
    let (tx, _rx) = tokio::sync::mpsc::channel(1);
    let req = TurnRequest::new("cancel", "hello", ".", Arc::new(Observer));
    let error = engine.run_turn(req, tx, cancel).await.unwrap_err();
    assert!(matches!(error, bcs_bridge_core::engine::TurnError::Aborted));
}

#[tokio::test]
async fn callback_private_fields_and_malformed_lines_never_enter_trace() {
    use bcs_bridge_core::engine::trace::{TraceStore, TraceContext};
    for case in ["midturn", "malformed"] {
        let dir = tempfile::tempdir().unwrap();
        let trace = TraceContext::new(TraceStore::open(dir.path()).unwrap(), "fixture", "run");
        let result = run_case_traced(case, Some("hook_idle"), Some(trace)).await;
        if case == "midturn" { assert!(result.is_ok()); } else { assert!(result.is_err()); }
        let text = std::fs::read_to_string(dir.path().join("engine.raw.ndjson")).unwrap();
        assert!(!text.contains("PRIVATE"), "hook fields must be redacted before raw tracing");
    }
}

#[tokio::test]
async fn observe_failures_do_not_change_legacy_output_acceptance() {
    for case in ["observe_large", "observe_init_failure", "observe_init_timeout"] {
        assert_eq!(run_case(case, "observe").await.expect(case).final_text.as_deref(), Some("DONE"));
    }
}

#[tokio::test]
async fn rejected_observer_callback_cannot_block_cancellation() {
    let error = run_case("nonreading_callback", "observe").await.expect_err("must cancel");
    assert!(error.contains("aborted"), "{error}");
}
