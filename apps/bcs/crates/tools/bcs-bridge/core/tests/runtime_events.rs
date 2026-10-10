use std::sync::Arc;
use bcs_bridge_core::{AppState, config::ProviderConfig, engine::EngineRegistry, runtime::{dispatch, DownstreamRequest, RuntimeReply}};
use serde_json::json;

fn state() -> (tempfile::TempDir, Arc<AppState>) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.toml");
    std::fs::write(&path, format!(r#"
provider_id = "bridge-1"
listen = "127.0.0.1:0"
bcs_to_provider_token = "test-only"
state_path = {:?}
[[bot]]
provider_bot_ref = "worker"
engine = "claude-code"
cwd = "/tmp"
"#, dir.path().join("state.sqlite3"))).unwrap();
    let state = Arc::new(AppState::new(ProviderConfig::load(&path).unwrap(), &EngineRegistry::builtin()).unwrap());
    (dir, state)
}

fn abort(id: &str, target: &str) -> DownstreamRequest {
    serde_json::from_value(json!({"id": id, "method": "chat.abort", "session_id": "session",
        "to_bot": {"provider_id": "bridge-1", "provider_bot_ref": "worker"}, "params": {"run_id": target}})).unwrap()
}

#[tokio::test]
async fn targeted_abort_never_cancels_a_newer_active_run() {
    let (_dir, state) = state();
    state.runs.begin("old", "old".into());
    state.runs.record_session("old", "worker", "session");
    state.runs.finish("old");
    let (new, _) = state.runs.begin("new", "new".into());
    state.runs.record_session("new", "worker", "session");
    state.sessions.try_start_run("worker", "session", "new").await.unwrap();
    let reply = dispatch(state.clone(), abort("abort-1", "old")).await.unwrap();
    assert!(matches!(reply, RuntimeReply::Json { status: 410, .. }));
    assert!(!new.abort.is_cancelled());
    let error = match dispatch(state.clone(), abort("abort-1", "new")).await {
        Err(error) => error,
        Ok(_) => panic!("target is part of the idempotency fingerprint"),
    };
    assert_eq!(error.code, "conflict");
    assert!(!new.abort.is_cancelled());
    assert!(matches!(dispatch(state, abort("abort-2", "new")).await.unwrap(), RuntimeReply::Json { status: 200, .. }));
    assert!(new.abort.is_cancelled());
}

#[tokio::test]
async fn targeted_abort_rejects_another_session_and_ignores_unknown_run() {
    let (_dir, state) = state();
    let (other, _) = state.runs.begin("other", "fp".into());
    state.runs.record_session("other", "worker", "different-session");
    let reply = dispatch(state.clone(), abort("abort-1", "other")).await;
    assert!(matches!(reply, Err(error) if error.code == "invalid_request"));
    assert!(!other.abort.is_cancelled());
    let reply = dispatch(state, abort("abort-2", "unknown")).await.unwrap();
    match reply {
        RuntimeReply::Json { status, body } => { assert_eq!(status, 200); assert_eq!(body["aborted"], false); }
        _ => panic!("abort must return an ordinary reply"),
    }
}
