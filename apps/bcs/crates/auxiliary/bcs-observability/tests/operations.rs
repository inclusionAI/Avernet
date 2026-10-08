use std::future::Future;
use std::io::Write;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tracing::instrument::WithSubscriber;

#[derive(Clone, Default)]
struct Buffer(Arc<Mutex<Vec<u8>>>);
impl Write for Buffer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}
async fn capture(future: impl Future<Output = ()>) -> String {
    let buffer = Buffer::default();
    let writer = buffer.clone();
    let subscriber = tracing_subscriber::fmt().json().with_max_level(tracing::Level::TRACE)
        .with_span_events(tracing_subscriber::fmt::format::FmtSpan::NEW)
        .with_writer(move || writer.clone()).finish();
    future.with_subscriber(subscriber).await;
    let bytes = buffer.0.lock().unwrap().clone();
    let logs = String::from_utf8(bytes).unwrap();
    for event in logs.lines().map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap()) {
        assert!(event["fields"].get("trace_id").is_none(), "unexpected log trace ID: {event}");
    }
    logs
}

#[tokio::test]
async fn observations_emit_logs_without_creating_spans() {
    let logs = capture(bcs_observability::with_request_context("logs-only".into(), async {
        bcs_observability::observe_result("test.parent", async {
            bcs_observability::count("test.cache", "miss");
            bcs_observability::observe_value("test.child", async {}).await;
            Ok::<_, ()>(())
        }).await.unwrap();
    })).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    assert!(events.iter().all(|event| event["fields"].get("trace_id").is_none()),
        "operation logs must use request/operation IDs without a trace ID: {logs}");
    assert!(events.iter().any(|event| event["fields"]["message"] == "http.request.operations"));
    assert_eq!(events.iter().filter(|event| event["fields"]["message"] == "bcs.operation.finished").count(), 2);
    assert!(events.iter().all(|event| event.get("span").is_none() && event.get("spans").is_none()),
        "operation logging must not create spans: {logs}");
}

#[tokio::test]
async fn result_and_error_are_preserved_without_logging_payloads() {
    let logs = capture(async {
        let value = bcs_observability::observe_result("test.read", async { Ok::<_, &str>("private-value") }).await;
        assert_eq!(value, Ok("private-value"));
        let error = bcs_observability::observe_result("test.read", async { Err::<(), _>("private-password") }).await;
        assert_eq!(error, Err("private-password"));
    }).await;
    assert!(logs.contains("bcs.operation.finished"));
    assert!(logs.contains("success"));
    assert!(logs.contains("error"));
    assert!(!logs.contains("private-value"));
    assert!(!logs.contains("private-password"));
    for line in logs.lines() {
        let json: serde_json::Value = serde_json::from_str(line).unwrap();
        if json["fields"]["message"] == "bcs.operation.finished" {
            assert!(json["fields"]["duration_ms"].as_f64().unwrap() >= 0.0);
        }
    }
}

#[tokio::test]
async fn cancelled_future_records_termination_and_is_not_successful() {
    let logs = capture(async {
        let result = tokio::time::timeout(Duration::from_millis(5),
            bcs_observability::observe_result("test.cancel", std::future::pending::<Result<(), ()>>())).await;
        assert!(result.is_err());
    }).await;
    assert!(logs.contains("cancelled"));
    assert!(!logs.contains("\"outcome\":\"success\""));
}

#[tokio::test]
async fn spawned_work_keeps_request_correlation_and_counts_fallbacks() {
    let logs = capture(bcs_observability::with_request_context("request-42".into(), async {
        bcs_observability::count("test.cache", "miss");
        tokio::spawn(bcs_observability::in_current_context(async {
            assert_eq!(bcs_observability::current_request_id(), "request-42");
            let _ = bcs_observability::observe_result("test.read", async { Err::<(), _>(()) }).await;
            bcs_observability::observe_result("test.read", async { Ok::<_, ()>(()) }).await.unwrap();
        })).await.unwrap();
    })).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let summary = events.iter().find(|event| event["fields"]["message"] == "http.request.operations").unwrap();
    assert_eq!(summary["fields"]["request_id"], "request-42");
    let observations: serde_json::Value = serde_json::from_str(summary["fields"]["observations"].as_str().unwrap()).unwrap();
    assert_eq!(observations["test.read"]["count"], 2);
    assert_eq!(observations["test.read"]["outcomes"]["error"], 1);
    assert_eq!(observations["test.read"]["outcomes"]["success"], 1);
    assert_eq!(observations["test.cache"]["outcomes"]["miss"], 1);
    for event in events.iter().filter(|event| event["fields"]["message"] == "bcs.operation.finished") {
        assert_eq!(event["fields"]["request_id"], "request-42");
    }
    assert_eq!(bcs_observability::current_request_id(), "");
}

#[tokio::test(start_paused = true)]
async fn stalled_warning_does_not_timeout_or_repeat() {
    let logs = capture(async {
        let (tx, rx) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(bcs_observability::in_current_context(
            bcs_observability::observe_result("test.slow", rx)));
        tokio::task::yield_now().await;
        tokio::time::advance(Duration::from_secs(6)).await;
        tokio::task::yield_now().await;
        assert!(!task.is_finished());
        tokio::time::advance(Duration::from_secs(10)).await;
        tokio::task::yield_now().await;
        assert!(!task.is_finished());
        tx.send(42).unwrap();
        assert_eq!(task.await.unwrap().unwrap(), 42);
    }).await;
    assert_eq!(logs.matches("bcs.operation.stalled").count(), 1);
    assert!(logs.contains("success"));
    assert!(!logs.contains("cancelled"));
}

#[tokio::test]
async fn long_lived_work_keeps_only_request_identity_after_handshake_finishes() {
    let logs = capture(async {
        let (release, ready) = tokio::sync::oneshot::channel();
        let task = bcs_observability::with_request_context("ws-handshake-42".into(), async {
            bcs_observability::observe_value("test.handshake", async {}).await;
            let request_id = bcs_observability::current_request_id();
            tokio::spawn(bcs_observability::with_request_id(request_id, async move {
                ready.await.unwrap();
                assert_eq!(bcs_observability::current_request_id(), "ws-handshake-42");
                bcs_observability::observe_value("test.ws_frame", async {}).await;
                tokio::spawn(bcs_observability::in_current_context(async {
                    tracing::warn!(request_id = %bcs_observability::CurrentRequestId, "old WS business error");
                })).await.unwrap();
            }).with_current_subscriber())
        }).await;
        release.send(()).unwrap();
        task.await.unwrap();
        assert_eq!(bcs_observability::current_request_id(), "");
    }).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let summaries: Vec<_> = events.iter().filter(|event| event["fields"]["message"] == "http.request.operations").collect();
    assert_eq!(summaries.len(), 1, "WS lifetime must not emit another HTTP summary");
    let totals: serde_json::Value = serde_json::from_str(summaries[0]["fields"]["observations"].as_str().unwrap()).unwrap();
    assert_eq!(totals["test.handshake"]["count"], 1);
    assert!(totals.get("test.ws_frame").is_none());
    let error = events.iter().find(|event| event["fields"]["message"] == "old WS business error").unwrap();
    assert_eq!(error["fields"]["request_id"], "ws-handshake-42");
    assert!(events.iter().all(|event| event.get("span").is_none() && event.get("spans").is_none()));
}

#[tokio::test]
async fn non_send_error_can_be_discarded_by_send_caller() {
    tokio::spawn(async {
        let failed = bcs_observability::observe_result("test.non_send", async {
            Err::<(), Box<dyn std::error::Error>>("expected".into())
        }).await.is_err();
        tokio::task::yield_now().await;
        assert!(failed);
    }).await.unwrap();
}

#[tokio::test]
async fn nested_and_spawned_operations_report_the_parent_id() {
    let logs = capture(bcs_observability::observe_value("test.parent", async {
        let parent = bcs_observability::current_operation_id();
        assert!(!parent.is_empty());
        tokio::spawn(bcs_observability::in_current_context(async move {
            assert_eq!(bcs_observability::current_operation_id(), parent);
            bcs_observability::observe_value("test.child", async {}).await;
        })).await.unwrap();
    })).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let parent = events.iter().find(|e| e["fields"]["operation"] == "test.parent").unwrap();
    let child = events.iter().find(|e| e["fields"]["operation"] == "test.child").unwrap();
    assert_eq!(child["fields"]["parent_operation_id"], parent["fields"]["operation_id"]);
    assert_eq!(bcs_observability::current_operation_id(), "");
}

#[tokio::test]
async fn request_scopes_restore_after_nested_work_and_cancellation() {
    let logs = capture(bcs_observability::with_request_context("outer-request".into(), async {
        let detached = bcs_observability::with_request_context("inner-request".into(), async {
            assert_eq!(bcs_observability::current_request_id(), "inner-request");
            bcs_observability::in_current_context(async {
                assert_eq!(bcs_observability::current_request_id(), "inner-request");
                bcs_observability::observe_value("test.detached", async {}).await;
            })
        }).await;
        assert_eq!(bcs_observability::current_request_id(), "outer-request");
        tokio::spawn(detached).await.unwrap();
        tokio::spawn(async { assert_eq!(bcs_observability::current_request_id(), ""); }).await.unwrap();
        let result = tokio::time::timeout(Duration::from_millis(5),
            bcs_observability::with_request_context("cancelled-request".into(), std::future::pending::<()>())).await;
        assert!(result.is_err());
        assert_eq!(bcs_observability::current_request_id(), "outer-request");
    })).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let operation = events.iter().find(|e| e["fields"]["operation"] == "test.detached").unwrap();
    assert_eq!(operation["fields"]["request_id"], "inner-request");
    assert_eq!(bcs_observability::current_request_id(), "");
}

#[tokio::test]
async fn concurrent_request_scopes_do_not_leak() {
    let barrier = Arc::new(tokio::sync::Barrier::new(2));
    let request = |request_id: &'static str| {
        let barrier = barrier.clone();
        bcs_observability::with_request_context(request_id.into(), async move {
            barrier.wait().await;
            tokio::task::yield_now().await;
            assert_eq!(bcs_observability::current_request_id(), request_id);
        })
    };
    tokio::join!(request("request-a"), request("request-b"));
    assert_eq!(bcs_observability::current_request_id(), "");
}

#[tokio::test]
async fn request_id_log_value_formats_the_emitting_context_without_leaking_between_requests() {
    let logs = capture(async {
        // The value is reusable: it reads the emitting context, not the context
        // where it was constructed. Owned IDs remain necessary across tasks.
        let field = bcs_observability::CurrentRequestId;
        let barrier = tokio::sync::Barrier::new(2);
        let emit = |id: &'static str| bcs_observability::with_request_id(id.into(), async {
            barrier.wait().await;
            tracing::warn!(request_id = %field, "request context error");
        });
        tokio::join!(emit("request-a"), emit("request-b"));
        tracing::warn!(request_id = %field, "outside request context");
    }).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let mut ids: Vec<_> = events.iter().filter(|event| event["fields"]["message"] == "request context error")
        .map(|event| event["fields"]["request_id"].as_str().unwrap()).collect();
    ids.sort_unstable();
    assert_eq!(ids, ["request-a", "request-b"]);
    let outside = events.iter().find(|event| event["fields"]["message"] == "outside request context").unwrap();
    assert_eq!(outside["fields"]["request_id"], "");
    assert!(events.iter().all(|event| event.get("span").is_none() && event.get("spans").is_none()));
}

#[test]
fn process_identity_is_shared_by_threads_and_changes_in_a_new_process() {
    let id = bcs_observability::process_instance_id();
    uuid::Uuid::parse_str(id).expect("process identity is a UUID");
    if std::env::var_os("BCS_OBSERVABILITY_PROCESS_PROBE").is_some() {
        println!("PROCESS_INSTANCE_ID={id}");
        return;
    }
    std::thread::scope(|scope| {
        let threads: Vec<_> = (0..8).map(|_| scope.spawn(bcs_observability::process_instance_id)).collect();
        for thread in threads { assert_eq!(thread.join().unwrap(), id); }
    });
    let child = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "process_identity_is_shared_by_threads_and_changes_in_a_new_process", "--nocapture"])
        .env("BCS_OBSERVABILITY_PROCESS_PROBE", "1")
        .output().expect("start a fresh process to verify restart identity");
    assert!(child.status.success(), "child process failed ({}): stdout={} stderr={}",
        child.status, String::from_utf8_lossy(&child.stdout), String::from_utf8_lossy(&child.stderr));
    let output = String::from_utf8(child.stdout).unwrap();
    let child_id = output.lines().find_map(|line| line.strip_prefix("PROCESS_INSTANCE_ID=")).expect("child identity");
    uuid::Uuid::parse_str(child_id).expect("child process identity is a UUID");
    assert_ne!(child_id, id);
}
