#![cfg(unix)]
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use bcs_bridge_core::engine::codex_app_server::CodexAppServer;
use bcs_bridge_core::engine::{Engine, SessionObserver, TurnError, TurnOutcome, TurnRequest};
use serde_json::Value;
use tokio_util::sync::CancellationToken;

struct Observer;
#[async_trait::async_trait]
impl SessionObserver for Observer {
    async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
}

fn engine(log: &std::path::Path) -> Arc<CodexAppServer> {
    Arc::new(CodexAppServer::new(PathBuf::from("/usr/bin/python3"), vec![
        format!("{}/tests/fixtures/mock_codex_persistent.py", env!("CARGO_MANIFEST_DIR")),
        log.to_string_lossy().into_owned(),
    ]))
}

fn requests(log: &std::path::Path) -> Vec<Value> {
    std::fs::read_to_string(log).unwrap_or_default().lines()
        .map(|line| serde_json::from_str(line).unwrap()).collect()
}

async fn wait_method(log: &std::path::Path, method: &str, count: usize) {
    tokio::time::timeout(Duration::from_secs(5), async {
        while requests(log).iter().filter(|value| value["method"] == method).count() < count {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
}

async fn turn(engine: &CodexAppServer, prompt: &str, thread: Option<String>, abort: CancellationToken) -> Result<TurnOutcome, TurnError> {
    let mut req = TurnRequest::new(uuid::Uuid::new_v4().to_string(), prompt, "/tmp", Arc::new(Observer));
    req.engine_session_id = thread;
    let (tx, mut rx) = tokio::sync::mpsc::channel(128);
    let drain = tokio::spawn(async move { while rx.recv().await.is_some() {} });
    let result = tokio::time::timeout(Duration::from_secs(10), engine.run_turn(req, tx, abort)).await.unwrap();
    drain.await.unwrap();
    result
}

#[tokio::test]
async fn consecutive_turns_reuse_process_and_loaded_thread_without_resuming() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let first = turn(&engine, "first", None, CancellationToken::new()).await.unwrap();
    let thread = first.engine_session_id.clone();
    let second = turn(&engine, "second", thread.clone(), CancellationToken::new()).await.unwrap();
    let third = turn(&engine, "third", thread, CancellationToken::new()).await.unwrap();
    assert_eq!(second.engine_session_id, first.engine_session_id);
    assert_eq!(third.engine_session_id, first.engine_session_id);
    let calls = requests(&log);
    assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 1);
    assert_eq!(calls.iter().filter(|v| v["method"] == "thread/start").count(), 1);
    assert!(!calls.iter().any(|v| v["method"] == "thread/resume"));
    assert_eq!(calls.iter().filter(|v| v["method"] == "turn/start").count(), 3);
    assert!(calls.iter().all(|v| v["pid"] == calls[0]["pid"]));
    let pid = calls[0]["pid"].as_i64().unwrap() as i32;
    drop(engine);
    tokio::time::sleep(Duration::from_millis(300)).await;
    // We own this fixture process; no desktop or user's Codex is signalled.
    assert!(!std::process::Command::new("kill").args(["-0", &pid.to_string()])
        .stderr(std::process::Stdio::null()).status().unwrap().success());
}

#[tokio::test]
async fn crash_fails_current_turn_and_next_request_restarts_and_resumes() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let first = turn(&engine, "first", None, CancellationToken::new()).await.unwrap();
    assert!(turn(&engine, "crash", first.engine_session_id.clone(), CancellationToken::new()).await.is_err());
    let next = turn(&engine, "next", first.engine_session_id.clone(), CancellationToken::new()).await.unwrap();
    assert_eq!(next.engine_session_id, first.engine_session_id);
    let calls = requests(&log);
    assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 2);
    assert_eq!(calls.iter().filter(|v| v["method"] == "thread/resume").count(), 1);
    assert_eq!(calls.iter().filter(|v| v.pointer("/params/input/0/text").and_then(Value::as_str) == Some("crash")).count(), 1);
    assert_ne!(calls[0]["pid"], calls.last().unwrap()["pid"]);
}

#[tokio::test]
async fn concurrent_threads_are_isolated_and_cancellation_keeps_server_alive() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let abort = CancellationToken::new();
    let held = {
        let engine = engine.clone(); let abort = abort.clone();
        tokio::spawn(async move { turn(&engine, "hold", None, abort).await })
    };
    wait_method(&log, "turn/start", 1).await;
    let other = turn(&engine, "other", None, CancellationToken::new()).await.unwrap();
    assert!(other.final_text.as_deref().unwrap().ends_with(":other"));
    assert!(!held.is_finished(), "another thread's completion must not finish held turn");
    abort.cancel();
    assert!(matches!(held.await.unwrap(), Err(TurnError::Aborted)));
    wait_method(&log, "turn/interrupt", 1).await;
    let followup = turn(&engine, "followup", other.engine_session_id, CancellationToken::new()).await.unwrap();
    assert!(followup.final_text.as_deref().unwrap().ends_with(":followup"));
    let calls = requests(&log);
    let interrupted = calls.iter().find(|v| v["method"] == "turn/interrupt").unwrap();
    let held_thread = &calls.iter().find(|v| v["method"] == "turn/start").unwrap()["params"]["threadId"];
    assert_eq!(&interrupted["params"]["threadId"], held_thread);
    assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 1);
}

#[tokio::test]
async fn cancellation_before_turn_start_response_interrupts_late_turn() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let abort = CancellationToken::new();
    let held = {
        let engine = engine.clone(); let abort = abort.clone();
        tokio::spawn(async move { turn(&engine, "late-start", None, abort).await })
    };
    wait_method(&log, "turn/start", 1).await;
    abort.cancel();
    assert!(matches!(held.await.unwrap(), Err(TurnError::Aborted)));
    wait_method(&log, "turn/interrupt", 1).await;
    turn(&engine, "next", None, CancellationToken::new()).await.unwrap();
    assert_eq!(requests(&log).iter().filter(|v| v["method"] == "initialize").count(), 1);
}

#[tokio::test]
async fn shutdown_reaps_idle_server_and_another_engine_resumes_saved_thread() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let first_engine = engine(&log);
    let first = turn(&first_engine, "first", None, CancellationToken::new()).await.unwrap();
    let pid = requests(&log)[0]["pid"].as_i64().unwrap().to_string();
    first_engine.shutdown().await;
    assert!(!std::process::Command::new("kill").args(["-0", &pid])
        .stderr(std::process::Stdio::null()).status().unwrap().success());
    assert!(turn(&first_engine, "stopped", first.engine_session_id.clone(), CancellationToken::new()).await.is_err());
    let replacement = engine(&log);
    let next = turn(&replacement, "next", first.engine_session_id.clone(), CancellationToken::new()).await.unwrap();
    assert_eq!(next.engine_session_id, first.engine_session_id);
    let calls = requests(&log);
    assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 2);
    assert_eq!(calls.iter().filter(|v| v["method"] == "thread/start").count(), 1);
    assert_eq!(calls.iter().filter(|v| v["method"] == "thread/resume").count(), 1);
}

#[tokio::test]
async fn dropping_inflight_future_interrupts_only_its_turn_and_allows_followup() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let held = {
        let engine = engine.clone();
        tokio::spawn(async move { turn(&engine, "hold", None, CancellationToken::new()).await })
    };
    wait_method(&log, "turn/start", 1).await;
    held.abort();
    assert!(held.await.unwrap_err().is_cancelled());
    wait_method(&log, "turn/interrupt", 1).await;
    let calls = requests(&log);
    let thread = calls.iter().find(|v| v["method"] == "turn/start").unwrap()["params"]["threadId"].as_str().unwrap().to_owned();
    turn(&engine, "next", Some(thread), CancellationToken::new()).await.unwrap();
    assert_eq!(requests(&log).iter().filter(|v| v["method"] == "initialize").count(), 1);
    assert!(!requests(&log).iter().any(|v| v["method"] == "thread/resume"));
}

#[tokio::test]
async fn failed_turn_preserves_loaded_thread_and_does_not_restart_process() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let first = turn(&engine, "first", None, CancellationToken::new()).await.unwrap();
    let error = turn(&engine, "fail", first.engine_session_id.clone(), CancellationToken::new()).await.unwrap_err();
    assert!(error.to_string().contains("test failure"));
    turn(&engine, "next", first.engine_session_id, CancellationToken::new()).await.unwrap();
    assert_eq!(requests(&log).iter().filter(|v| v["method"] == "initialize").count(), 1);
    assert!(!requests(&log).iter().any(|v| v["method"] == "thread/resume"));
}

#[tokio::test]
async fn externally_owned_thread_is_reported_without_forking_or_replaying() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let error = turn(&engine, "message", Some("external-owned".into()), CancellationToken::new()).await.unwrap_err();
    assert!(error.to_string().contains("active writer"));
    let calls = requests(&log);
    assert_eq!(calls.iter().filter(|v| v["method"] == "thread/resume").count(), 1);
    assert!(!calls.iter().any(|v| v["method"] == "thread/start" || v["method"] == "turn/start"));
}

#[tokio::test]
async fn unacknowledged_interrupt_reaps_server_before_next_request_resumes() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let abort = CancellationToken::new();
    let held = {
        let engine = engine.clone(); let abort = abort.clone();
        tokio::spawn(async move { turn(&engine, "ignore-interrupt", None, abort).await })
    };
    wait_method(&log, "turn/start", 1).await;
    let calls = requests(&log);
    let thread = calls.iter().find(|v| v["method"] == "turn/start").unwrap()["params"]["threadId"].as_str().unwrap().to_owned();
    abort.cancel();
    assert!(matches!(held.await.unwrap(), Err(TurnError::Aborted)));
    turn(&engine, "next", Some(thread), CancellationToken::new()).await.unwrap();
    let calls = requests(&log);
    assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 2);
    assert_eq!(calls.iter().filter(|v| v["method"] == "thread/resume").count(), 1);
}
