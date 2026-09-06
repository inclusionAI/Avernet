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
    String::from_utf8(bytes).unwrap()
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
async fn trace_strings_are_scoped_and_propagated_without_a_tracing_sdk() {
    let logs = capture(bcs_observability::with_trace_id("outer-trace".into(),
        bcs_observability::with_request_context("request-with-trace".into(), async {
            let detached = bcs_observability::with_trace_id("inner-trace".into(), async {
                assert_eq!(bcs_observability::current_trace_id(), "inner-trace");
                bcs_observability::in_current_context(async {
                    assert_eq!(bcs_observability::current_trace_id(), "inner-trace");
                    assert_eq!(bcs_observability::current_request_id(), "request-with-trace");
                    bcs_observability::observe_value("test.trace", async {}).await;
                })
            }).await;
            assert_eq!(bcs_observability::current_trace_id(), "outer-trace");
            tokio::spawn(detached).await.unwrap();
            tokio::spawn(async { assert_eq!(bcs_observability::current_trace_id(), ""); }).await.unwrap();
            let result = tokio::time::timeout(Duration::from_millis(5),
                bcs_observability::with_trace_id("cancelled-trace".into(), std::future::pending::<()>())).await;
            assert!(result.is_err());
            assert_eq!(bcs_observability::current_trace_id(), "outer-trace");
        }))).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let operation = events.iter().find(|e| e["fields"]["operation"] == "test.trace").unwrap();
    let summary = events.iter().find(|e| e["fields"]["message"] == "http.request.operations").unwrap();
    assert_eq!(operation["fields"]["trace_id"], "inner-trace");
    assert_eq!(summary["fields"]["trace_id"], "outer-trace");
    assert_eq!(bcs_observability::current_trace_id(), "");
}

#[tokio::test]
async fn concurrent_trace_scopes_do_not_leak_between_requests() {
    let barrier = Arc::new(tokio::sync::Barrier::new(2));
    let request = |trace_id: &'static str| {
        let barrier = barrier.clone();
        bcs_observability::with_trace_id(trace_id.into(), async move {
            barrier.wait().await;
            tokio::task::yield_now().await;
            assert_eq!(bcs_observability::current_trace_id(), trace_id);
        })
    };
    tokio::join!(request("trace-a"), request("trace-b"));
    assert_eq!(bcs_observability::current_trace_id(), "");
}
