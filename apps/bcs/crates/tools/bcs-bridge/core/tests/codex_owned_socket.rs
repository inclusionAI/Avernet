#![cfg(unix)]
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use bcs_bridge_core::engine::codex_app_server::CodexAppServerFactory;
use bcs_bridge_core::engine::{Engine, EngineFactory, SessionObserver, TurnError, TurnOutcome, TurnRequest};
use serde_json::Value;
use tokio_util::sync::CancellationToken;

struct Observer;
#[async_trait::async_trait]
impl SessionObserver for Observer {
    async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
}

fn engine(log: &Path, options: &toml::Table) -> Arc<dyn Engine> {
    let python = std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .map(|dir| dir.join("python3")).find(|path| path.is_file()).expect("python3 fixture interpreter");
    let factory = CodexAppServerFactory::custom("test-owned-socket", "python3", vec![
        format!("{}/tests/fixtures/mock_codex_socket.py", env!("CARGO_MANIFEST_DIR")),
        log.to_string_lossy().into_owned(),
    ]).with_owned_unix_socket();
    assert_eq!(factory.id(), "test-owned-socket");
    assert!(factory.supports_caller_env());
    factory.build(python, options).unwrap()
}

fn requests(log: &Path) -> Vec<Value> {
    std::fs::read_to_string(log).unwrap_or_default().lines()
        .filter_map(|line| serde_json::from_str(line).ok()).collect()
}

async fn wait_method(log: &Path, method: &str, count: usize) {
    tokio::time::timeout(Duration::from_secs(5), async {
        while requests(log).iter().filter(|v| v["method"] == method).count() < count {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.expect("fixture method observed");
}

fn alive(pid: i64) -> bool {
    #[cfg(target_os = "linux")]
    if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        if stat.rsplit_once(") ").is_some_and(|(_, rest)| rest.starts_with('Z')) { return false; }
    }
    std::process::Command::new("kill").args(["-0", &pid.to_string()])
        .stderr(std::process::Stdio::null()).status().unwrap().success()
}

async fn assert_reaped(log: &Path) {
    let spawns: Vec<_> = requests(log).into_iter().filter(|v| v["method"] == "fixture/spawn").collect();
    assert!(!spawns.is_empty());
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let gone = spawns.iter().all(|v| !alive(v["pid"].as_i64().unwrap())
                && v["grandchild"].as_i64().is_none_or(|pid| !alive(pid))
                && !Path::new(v["socket"].as_str().unwrap()).parent().unwrap().exists());
            if gone { break; }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }).await.expect("owned process tree and socket directory reaped");
}

async fn turn(engine: Arc<dyn Engine>, log: &Path, prompt: &str, thread: Option<String>,
    env: &[(&str, &str)], abort: CancellationToken) -> Result<TurnOutcome, TurnError> {
    let mut req = TurnRequest::new(uuid::Uuid::new_v4().to_string(), prompt,
        log.parent().unwrap().to_path_buf(), Arc::new(Observer));
    req.engine_session_id = thread;
    req.env = env.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
    let (tx, mut rx) = tokio::sync::mpsc::channel(128);
    let drain = tokio::spawn(async move { while rx.recv().await.is_some() {} });
    let result = tokio::time::timeout(Duration::from_secs(12), engine.run_turn(req, tx, abort))
        .await.expect("turn bounded by test deadline");
    drain.await.unwrap();
    result
}

#[tokio::test]
async fn socket_reuses_process_preserves_policy_and_drains_large_diagnostics() {
    use std::os::unix::fs::PermissionsExt;
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let options: toml::Table = toml::from_str("sandbox = 'workspace-write'\nnetwork_access = true\napproval_policy = 'on-request'").unwrap();
    let engine = engine(&log, &options);
    let large = "large-你好-".repeat(20_000);
    let first = turn(engine.clone(), &log, &large, None,
        &[("MOCK_DIAGNOSTIC_BYTES", "262144"), ("MOCK_SOCKET_READY_DELAY", "0.15")], CancellationToken::new()).await.unwrap();
    assert!(first.final_text.as_ref().unwrap().ends_with(&large));
    let second = turn(engine.clone(), &log, "second", first.engine_session_id.clone(), &[], CancellationToken::new()).await.unwrap();
    assert_eq!(second.engine_session_id, first.engine_session_id);
    let calls = requests(&log);
    assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 1);
    assert!(!calls.iter().any(|v| v["method"] == "thread/resume"));
    let start = calls.iter().find(|v| v["method"] == "thread/start").unwrap();
    assert_eq!(start["params"]["sandbox"], "workspace-write");
    assert_eq!(start["params"]["approvalPolicy"], "on-request");
    let run = calls.iter().find(|v| v["method"] == "turn/start").unwrap();
    assert_eq!(run["params"]["sandboxPolicy"]["networkAccess"], true);
    let dir = Path::new(calls[0]["socket"].as_str().unwrap()).parent().unwrap();
    assert_eq!(std::fs::metadata(dir).unwrap().permissions().mode() & 0o777, 0o700);
    engine.shutdown().await;
    assert_reaped(&log).await;
}

#[tokio::test]
async fn socket_concurrent_turns_and_cancel_keep_other_thread_alive() {
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log, &toml::Table::new());
    let abort = CancellationToken::new();
    let held = { let e = engine.clone(); let l = log.clone(); let a = abort.clone();
        tokio::spawn(async move { turn(e, &l, "hold", None, &[], a).await }) };
    wait_method(&log, "turn/start", 1).await;
    let other = turn(engine.clone(), &log, "other", None, &[], CancellationToken::new()).await.unwrap();
    assert!(other.final_text.unwrap().ends_with(":other"));
    assert!(!held.is_finished());
    abort.cancel();
    assert!(matches!(held.await.unwrap(), Err(TurnError::Aborted)));
    turn(engine.clone(), &log, "followup", other.engine_session_id, &[], CancellationToken::new()).await.unwrap();
    let calls = requests(&log);
    assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 1);
    let interrupt = calls.iter().find(|v| v["method"] == "turn/interrupt").unwrap();
    let first = calls.iter().find(|v| v["method"] == "turn/start").unwrap();
    assert_eq!(interrupt["params"]["threadId"], first["params"]["threadId"]);
    engine.shutdown().await; assert_reaped(&log).await;
}

#[tokio::test]
async fn socket_disconnect_reaps_live_engine_and_next_request_resumes_without_replay() {
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log, &toml::Table::new());
    let first = turn(engine.clone(), &log, "first", None,
        &[("MOCK_SOCKET_GRANDCHILD", "1")], CancellationToken::new()).await.unwrap();
    assert!(turn(engine.clone(), &log, "disconnect", first.engine_session_id.clone(), &[], CancellationToken::new()).await.is_err());
    assert_reaped(&log).await;
    let next = turn(engine.clone(), &log, "after", first.engine_session_id.clone(), &[], CancellationToken::new()).await.unwrap();
    assert_eq!(next.engine_session_id, first.engine_session_id);
    let calls = requests(&log);
    let spawns: Vec<_> = calls.iter().filter(|v| v["method"] == "fixture/spawn").collect();
    assert_eq!(spawns.len(), 2);
    assert_ne!(spawns[0]["pid"], spawns[1]["pid"]);
    assert_ne!(spawns[0]["socket"], spawns[1]["socket"]);
    assert_eq!(calls.iter().filter(|v| v["method"] == "thread/resume").count(), 1);
    assert_eq!(calls.iter().filter(|v| v["method"] == "fixture/disconnect").count(), 1, "failed request must not replay");
    engine.shutdown().await; assert_reaped(&log).await;
}

#[tokio::test]
async fn socket_startup_cancellation_reaps_child_before_readiness_or_handshake() {
    for phase in ["no-listen", "no-handshake"] {
        let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
        let engine = engine(&log, &toml::Table::new());
        let abort = CancellationToken::new();
        let task = { let e = engine.clone(); let l = log.clone(); let a = abort.clone();
            tokio::spawn(async move { turn(e, &l, "never", None, &[("MOCK_SOCKET_STARTUP", phase)], a).await }) };
        wait_method(&log, "fixture/spawn", 1).await;
        // Let the handshake case reach its accepted, silent socket.
        tokio::time::sleep(Duration::from_millis(100)).await;
        abort.cancel();
        assert!(matches!(task.await.unwrap(), Err(TurnError::Aborted)));
        assert_reaped(&log).await;
        engine.shutdown().await;
    }
}

#[tokio::test]
async fn socket_invalid_endpoint_and_initialize_failure_do_not_fall_back_or_leak() {
    for env in [[("MOCK_SOCKET_STARTUP", "unsafe-file")], [("MOCK_SOCKET_INIT_ERROR", "1")]] {
        let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
        let engine = engine(&log, &toml::Table::new());
        assert!(turn(engine.clone(), &log, "never", None, &env, CancellationToken::new()).await.is_err());
        assert_reaped(&log).await;
        assert_eq!(requests(&log).iter().filter(|v| v["method"] == "fixture/spawn").count(), 1);
        engine.shutdown().await;
    }
}

#[tokio::test]
async fn socket_instances_keep_endpoints_caller_environment_and_workspace_isolated() {
    let temp = tempfile::tempdir().unwrap();
    let mut snapshots = Vec::new();
    let mut engines = Vec::new();
    for actor in ["actor-a", "actor-b"] {
        let cwd = temp.path().join(actor); std::fs::create_dir(&cwd).unwrap();
        let log = cwd.join("rpc.jsonl"); let e = engine(&log, &toml::Table::new());
        turn(e.clone(), &log, actor, None, &[("BCN_ACTOR_BOT_ID", actor)], CancellationToken::new()).await.unwrap();
        let calls = requests(&log);
        assert!(calls.iter().all(|v| v["actor"] == actor));
        assert_eq!(PathBuf::from(calls[0]["cwd"].as_str().unwrap()), cwd);
        snapshots.push(calls[0].clone()); engines.push((e, log));
    }
    assert_ne!(snapshots[0]["socket"], snapshots[1]["socket"]);
    for (e, log) in engines { e.shutdown().await; assert_reaped(&log).await; }
}

#[tokio::test]
async fn dropping_owned_socket_engine_reaps_idle_process() {
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let e = engine(&log, &toml::Table::new());
    turn(e.clone(), &log, "first", None, &[], CancellationToken::new()).await.unwrap();
    drop(e);
    assert_reaped(&log).await;
}

#[tokio::test]
async fn socket_shutdown_kills_descendant_even_when_group_leader_exits_first() {
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let e = engine(&log, &toml::Table::new());
    turn(e.clone(), &log, "first", None,
        &[("MOCK_SOCKET_STUBBORN_DESCENDANT", "1")], CancellationToken::new()).await.unwrap();
    e.shutdown().await;
    assert_reaped(&log).await;
}

#[tokio::test]
async fn socket_question_reply_is_scoped_and_does_not_block_other_turns() {
    use bcs_protocol::stream::{InteractionPhase, StreamEvent};
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let e = engine(&log, &toml::Table::new());
    let req = TurnRequest::new("question-run", "ask-roundtrip", temp.path(), Arc::new(Observer));
    let registry = req.interactions.clone();
    let (tx, mut rx) = tokio::sync::mpsc::channel(128);
    let handle = { let engine = e.clone();
        tokio::spawn(async move { engine.run_turn(req, tx, CancellationToken::new()).await }) };
    let requested = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if let StreamEvent::Interaction(event) = rx.recv().await.unwrap() {
                if event.phase == InteractionPhase::Requested { break event; }
            }
        }
    }).await.unwrap();
    assert!(!handle.is_finished());
    turn(e.clone(), &log, "other", None, &[], CancellationToken::new()).await.unwrap();
    let answer = serde_json::json!({"action":"submit","answers":{
        "choice":{"values":["custom"]},"text":{"values":[]}}});
    assert_eq!(registry.resolve(&requested.interaction_id, "reply", answer.clone()),
        bcs_bridge_core::interaction::ResolveOutcome::Delivered);
    assert_eq!(registry.resolve(&requested.interaction_id, "reply", answer),
        bcs_bridge_core::interaction::ResolveOutcome::Duplicate);
    let drain = tokio::spawn(async move { while rx.recv().await.is_some() {} });
    let outcome = tokio::time::timeout(Duration::from_secs(5), handle).await.unwrap().unwrap().unwrap();
    drain.await.unwrap();
    assert_eq!(serde_json::from_str::<Value>(outcome.final_text.as_deref().unwrap()).unwrap(),
        serde_json::json!({"answers":{"choice":{"answers":["custom"]},"text":{"answers":[]}}}));
    assert_eq!(requests(&log).iter().filter(|v| v.get("method").is_none()).count(), 1);
    e.shutdown().await; assert_reaped(&log).await;
}

#[tokio::test]
async fn socket_disconnect_fails_all_inflight_turns_then_recovers() {
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let e = engine(&log, &toml::Table::new());
    let held = { let engine = e.clone(); let log = log.clone();
        tokio::spawn(async move { turn(engine, &log, "hold", None, &[], CancellationToken::new()).await }) };
    wait_method(&log, "turn/start", 1).await;
    assert!(turn(e.clone(), &log, "disconnect", None, &[], CancellationToken::new()).await.is_err());
    assert!(tokio::time::timeout(Duration::from_secs(5), held).await.unwrap().unwrap().is_err());
    assert_reaped(&log).await;
    turn(e.clone(), &log, "recovered", None, &[], CancellationToken::new()).await.unwrap();
    e.shutdown().await; assert_reaped(&log).await;
}

#[tokio::test]
async fn socket_unacknowledged_interrupt_reaps_server_before_replacement() {
    let temp = tempfile::tempdir().unwrap(); let log = temp.path().join("rpc.jsonl");
    let e = engine(&log, &toml::Table::new());
    let abort = CancellationToken::new();
    let held = { let engine = e.clone(); let log = log.clone(); let abort = abort.clone();
        tokio::spawn(async move { turn(engine, &log, "ignore-interrupt", None, &[], abort).await }) };
    wait_method(&log, "turn/start", 1).await;
    abort.cancel();
    assert!(matches!(held.await.unwrap(), Err(TurnError::Aborted)));
    assert_reaped(&log).await;
    turn(e.clone(), &log, "after", None, &[], CancellationToken::new()).await.unwrap();
    assert_eq!(requests(&log).iter().filter(|v| v["method"] == "initialize").count(), 2);
    e.shutdown().await; assert_reaped(&log).await;
}
