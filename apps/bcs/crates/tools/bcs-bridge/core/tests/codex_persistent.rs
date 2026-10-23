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
    // Resolve python3 the way the shell would: /usr/bin/python3 is not
    // universal (some Linux images keep it under /usr/local), and a missing
    // interpreter made this suite fail deterministically far from the actual
    // assertion under test.
    let python = std::env::var("PATH").ok().and_then(|paths| {
        paths.split(':').map(PathBuf::from).find_map(|dir| {
            let candidate = dir.join("python3");
            candidate.is_file().then_some(candidate)
        })
    }).unwrap_or_else(|| PathBuf::from("/usr/bin/python3"));
    Arc::new(CodexAppServer::new(python, vec![
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
async fn persistent_servers_keep_each_bots_caller_environment_across_turns() {
    let temp = tempfile::tempdir().unwrap();
    let log_a = temp.path().join("actor-a.jsonl");
    let log_b = temp.path().join("actor-b.jsonl");
    async fn run_actor(engine: Arc<CodexAppServer>, actor: &str) {
        let mut session = None;
        for _ in 0..2 {
            let mut req = TurnRequest::new(actor, "hello", "/tmp", Arc::new(Observer));
            req.engine_session_id = session;
            req.env = vec![("BCN_ACTOR_BOT_ID".into(), actor.into()),
                ("BCN_AUTH_MODE".into(), "agentpass-subbots".into())];
            let (tx, mut rx) = tokio::sync::mpsc::channel(128);
            let drain = tokio::spawn(async move { while rx.recv().await.is_some() {} });
            let outcome = tokio::time::timeout(Duration::from_secs(10),
                engine.run_turn(req, tx, CancellationToken::new())).await.unwrap().unwrap();
            drain.await.unwrap();
            session = outcome.engine_session_id;
        }
        engine.shutdown().await;
    }
    tokio::join!(run_actor(engine(&log_a), "bot_A"), run_actor(engine(&log_b), "bot_B"));
    for (log, actor) in [(&log_a, "bot_A"), (&log_b, "bot_B")] {
        let calls = requests(log);
        assert!(!calls.is_empty());
        assert!(calls.iter().all(|call| call["actor"] == actor));
        assert_eq!(calls.iter().filter(|call| call["method"] == "initialize").count(), 1);
        assert_eq!(calls.iter().filter(|call| call["method"] == "turn/start").count(), 2);
        assert!(calls.iter().all(|call| call["pid"] == calls[0]["pid"]));
    }
    assert_ne!(requests(&log_a)[0]["pid"], requests(&log_b)[0]["pid"]);
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

#[tokio::test]
async fn cancellation_before_start_ack_waits_for_cleanup_before_returning() {
    for prompt in ["hold", "ignore-interrupt", "never-start"] {
        let temp = tempfile::tempdir().unwrap();
        let log = temp.path().join("rpc.jsonl");
        let engine = engine(&log);
        let abort = CancellationToken::new();
        let mut held = {
            let engine = engine.clone(); let abort = abort.clone();
            let prompt = format!("gated-start-{prompt}");
            tokio::spawn(async move { turn(&engine, &prompt, None, abort).await })
        };
        wait_method(&log, "turn/start", 1).await;
        let calls = requests(&log);
        let thread = calls.iter().find(|v| v["method"] == "turn/start").unwrap()["params"]["threadId"].as_str().unwrap().to_owned();
        abort.cancel();
        // The fixture cannot ACK until released. Cancellation must stay pending,
        // rather than letting the caller start another turn on the old process.
        assert!(tokio::time::timeout(Duration::from_millis(100), &mut held).await.is_err());
        if prompt != "never-start" {
            std::fs::write(log.with_extension("jsonl.release"), "release").unwrap();
        }
        assert!(matches!(held.await.unwrap(), Err(TurnError::Aborted)));
        turn(&engine, "next", Some(thread), CancellationToken::new()).await.unwrap();
        let calls = requests(&log);
        assert_eq!(calls.iter().filter(|v| v["method"] == "turn/interrupt").count(), usize::from(prompt != "never-start"));
        let restarted = usize::from(prompt != "hold");
        assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 1 + restarted);
        assert_eq!(calls.iter().filter(|v| v["method"] == "thread/resume").count(), restarted);
    }
}

#[tokio::test]
async fn delayed_interrupted_completion_cannot_finish_followup_on_same_thread() {
    // Release the old completion only when the follow-up starts: no scheduling
    // sleeps, and exercise delivery before/after its RPC ack and between deltas.
    for phase in ["before-ack", "after-ack", "during-output"] {
        for shape in ["nested", "flat", "conflicting"] {
            let temp = tempfile::tempdir().unwrap();
            let log = temp.path().join("rpc.jsonl");
            let engine = engine(&log);
            let abort = CancellationToken::new();
            let held = {
                let engine = engine.clone(); let abort = abort.clone();
                tokio::spawn(async move { turn(&engine, "hold-delayed-completion", None, abort).await })
            };
            wait_method(&log, "turn/start", 1).await;
            let calls = requests(&log);
            let thread = calls.iter().find(|v| v["method"] == "turn/start").unwrap()["params"]["threadId"].as_str().unwrap().to_owned();
            let pid = calls[0]["pid"].as_i64().unwrap();
            abort.cancel();
            assert!(matches!(held.await.unwrap(), Err(TurnError::Aborted)));
            let prompt = format!("followup-{phase}:{shape}");
            let next = turn(&engine, &prompt, Some(thread.clone()), CancellationToken::new()).await.unwrap();
            assert_eq!(next.final_text.as_deref(), Some(format!("{pid}:{prompt}").as_str()), "{phase}/{shape}");
            assert_eq!(next.engine_session_id.as_deref(), Some(thread.as_str()));
            turn(&engine, "third", Some(thread), CancellationToken::new()).await.unwrap();
            let calls = requests(&log);
            assert_eq!(calls.iter().filter(|v| v["method"] == "fixture/stale-completion").count(), 1);
            assert_eq!(calls.iter().filter(|v| v["method"] == "initialize").count(), 1);
            assert_eq!(calls.iter().filter(|v| v["method"] == "turn/interrupt").count(), 1);
            assert!(!calls.iter().any(|v| v["method"] == "thread/resume"));
        }
    }
}

async fn asked_turn(engine: Arc<CodexAppServer>, prompt: &str, abort: CancellationToken)
    -> (bcs_bridge_core::interaction::InteractionRegistry,
        tokio::sync::mpsc::Receiver<bcs_protocol::stream::StreamEvent>,
        tokio::task::JoinHandle<Result<TurnOutcome, TurnError>>) {
    let req = TurnRequest::new(uuid::Uuid::new_v4().to_string(), prompt, "/tmp", Arc::new(Observer));
    let registry = req.interactions.clone();
    let (tx, rx) = tokio::sync::mpsc::channel(128);
    let handle = tokio::spawn(async move { engine.run_turn(req, tx, abort).await });
    (registry, rx, handle)
}

async fn question_event(rx: &mut tokio::sync::mpsc::Receiver<bcs_protocol::stream::StreamEvent>, phase: bcs_protocol::stream::InteractionPhase)
    -> bcs_protocol::stream::InteractionEvent {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match rx.recv().await.expect("turn ended before interaction") {
                bcs_protocol::stream::StreamEvent::Interaction(event) if event.phase == phase => return event,
                _ => {},
            }
        }
    }).await.unwrap()
}

#[tokio::test]
async fn questions_roundtrip_custom_text_skip_and_cancel_without_blocking_other_threads() {
    use bcs_protocol::stream::InteractionPhase;
    use serde_json::json;
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    for cancel in [false, true] {
        let (registry, mut rx, handle) = asked_turn(engine.clone(), "ask-roundtrip", CancellationToken::new()).await;
        let requested = question_event(&mut rx, InteractionPhase::Requested).await;
        assert_eq!(requested.raw["questions"][0]["questionId"], "choice");
        assert_eq!(requested.raw["questions"][0]["options"][0]["description"], "first");
        assert!(!handle.is_finished());
        // Another conversation remains usable while this one is waiting.
        let other = turn(&engine, "other", None, CancellationToken::new()).await.unwrap();
        assert!(other.final_text.unwrap().ends_with(":other"));
        let answer = if cancel { json!({"action":"cancel"}) } else {
            json!({"action":"submit","answers":{"choice":{"values":["custom"]},"text":{"values":[]}}})
        };
        assert_eq!(registry.resolve(&requested.interaction_id,"answer-1",answer.clone()),
            bcs_bridge_core::interaction::ResolveOutcome::Delivered);
        assert_eq!(registry.resolve(&requested.interaction_id,"answer-1",answer),
            bcs_bridge_core::interaction::ResolveOutcome::Duplicate);
        question_event(&mut rx, InteractionPhase::Resolved).await;
        let outcome = tokio::time::timeout(Duration::from_secs(5), handle).await.unwrap().unwrap().unwrap();
        let result: Value = serde_json::from_str(outcome.final_text.as_deref().unwrap()).unwrap();
        assert_eq!(result, if cancel { json!({"answers":{}}) } else {
            json!({"answers":{"choice":{"answers":["custom"]},"text":{"answers":[]}}}) });
    }
    let replies: Vec<_> = requests(&log).into_iter().filter(|v| v.get("method").is_none()).collect();
    assert_eq!(replies.len(), 2, "each question gets exactly one native RPC response");
}

#[tokio::test]
async fn pending_questions_close_on_abort_crash_or_engine_resolution_and_ignore_late_answers() {
    use bcs_protocol::stream::InteractionPhase;
    use serde_json::json;
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    for prompt in ["ask-roundtrip", "async-request", "ask-crash", "ask-engine-close"] {
        let abort = CancellationToken::new();
        let (registry, mut rx, handle) = asked_turn(engine.clone(), prompt, abort.clone()).await;
        let requested = question_event(&mut rx, InteractionPhase::Requested).await;
        if matches!(prompt,"ask-roundtrip" | "async-request") { abort.cancel(); }
        let result = tokio::time::timeout(Duration::from_secs(5), handle).await.unwrap().unwrap();
        if prompt == "ask-engine-close" { assert!(result.is_ok()); } else { assert!(result.is_err()); }
        question_event(&mut rx, InteractionPhase::Resolved).await;
        assert_eq!(registry.resolve(&requested.interaction_id,"late",json!({"action":"submit","answers":{}})),
            bcs_bridge_core::interaction::ResolveOutcome::Duplicate);
    }
}

#[tokio::test]
async fn secret_and_wrong_turn_requests_are_rejected_without_exposing_a_form() {
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    for prompt in ["ask-secret", "ask-wrong-turn"] {
        let (_, mut rx, handle) = asked_turn(engine.clone(), prompt, CancellationToken::new()).await;
        let result = tokio::time::timeout(Duration::from_secs(5),handle).await.unwrap().unwrap().unwrap();
        assert!(result.final_text.unwrap().contains("-32000"));
        while let Some(event) = rx.recv().await {
            assert!(!matches!(event, bcs_protocol::stream::StreamEvent::Interaction(_)));
        }
    }
}

#[tokio::test]
async fn simultaneous_questions_return_only_to_their_own_codex_threads() {
    use bcs_protocol::stream::InteractionPhase;
    use serde_json::json;
    let temp = tempfile::tempdir().unwrap();
    let engine = engine(&temp.path().join("rpc.jsonl"));
    let (reg_a, mut rx_a, task_a) = asked_turn(engine.clone(), "ask-a", CancellationToken::new()).await;
    let (reg_b, mut rx_b, task_b) = asked_turn(engine.clone(), "ask-b", CancellationToken::new()).await;
    let a = question_event(&mut rx_a, InteractionPhase::Requested).await;
    let b = question_event(&mut rx_b, InteractionPhase::Requested).await;
    assert_ne!(a.interaction_id, b.interaction_id);
    assert_eq!(reg_a.resolve(&b.interaction_id,"wrong",json!({"action":"cancel"})),
        bcs_bridge_core::interaction::ResolveOutcome::Unknown);
    for (reg, iid, answer) in [(&reg_b, &b.interaction_id, "B"), (&reg_a, &a.interaction_id, "A")] {
        reg.resolve(iid,"answer",json!({"action":"submit","answers":{
            "choice":{"values":[answer]},"text":{"values":[]}}}));
    }
    for (task, expected) in [(task_a,"A"),(task_b,"B")] {
        let result = tokio::time::timeout(Duration::from_secs(5),task).await.unwrap().unwrap().unwrap();
        let response: Value = serde_json::from_str(result.final_text.as_deref().unwrap()).unwrap();
        assert_eq!(response["answers"]["choice"]["answers"],json!([expected]));
    }
}

#[tokio::test]
async fn async_question_metadata_survives_turn_completion_and_answers_resume_the_same_thread() {
    use bcs_protocol::stream::InteractionPhase;
    use serde_json::json;
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    for prompt in ["async-request", "async-snapshot-only"] {
        let (registry, mut rx, handle) = asked_turn(engine.clone(), prompt, CancellationToken::new()).await;
        let requested = question_event(&mut rx, InteractionPhase::Requested).await;
        assert_eq!(requested.raw["questions"][0]["question"],"你今天更想喝什么？");
        assert_eq!(requested.raw["questions"][0]["options"][1]["value"],"茶");
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(!handle.is_finished(),"BCN run must stay open after the native turn ends");
        registry.resolve(&requested.interaction_id,"answer",json!({"action":"submit","answers":{
            "question_1":{"values":["茶"]}}}));
        question_event(&mut rx, InteractionPhase::Resolved).await;
        let result = tokio::time::timeout(Duration::from_secs(5),handle).await.unwrap().unwrap().unwrap();
        let text = result.final_text.unwrap();
        assert!(text.contains("用户已回答") && text.contains("茶"),"{text}");
        assert!(!text.contains("Question sent."),"old final must not replace the follow-up answer");
        let starts: Vec<_> = requests(&log).into_iter().filter(|v| v["method"]=="turn/start").collect();
        let last = starts.len()-1;
        assert_eq!(starts[last]["params"]["threadId"], starts[last-1]["params"]["threadId"]);
    }
    assert!(requests(&log).iter().all(|v| v.get("method").is_some()),
        "async questions have no native RPC request to respond to");
}

#[tokio::test]
async fn cancelling_an_async_question_finishes_without_starting_an_answer_turn() {
    use bcs_protocol::stream::InteractionPhase;
    use serde_json::json;
    let temp = tempfile::tempdir().unwrap();
    let log = temp.path().join("rpc.jsonl");
    let engine = engine(&log);
    let (registry, mut rx, handle) = asked_turn(engine,"async-request",CancellationToken::new()).await;
    let requested = question_event(&mut rx,InteractionPhase::Requested).await;
    registry.resolve(&requested.interaction_id,"cancel",json!({"action":"cancel"}));
    let result = tokio::time::timeout(Duration::from_secs(5),handle).await.unwrap().unwrap().unwrap();
    assert_eq!(result.final_text.as_deref(),Some("Question sent."));
    assert_eq!(requests(&log).iter().filter(|v| v["method"]=="turn/start").count(),1);
}
