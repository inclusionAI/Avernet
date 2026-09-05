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
        .with_writer(move || writer.clone()).finish();
    future.with_subscriber(subscriber).await;
    let bytes = buffer.0.lock().unwrap().clone();
    String::from_utf8(bytes).unwrap()
}

#[tokio::test]
async fn result_and_error_are_preserved_without_logging_payloads() {
    let logs = capture(async {
        let value = bcs_telemetry::observe_result("test.read", async { Ok::<_, &str>("private-value") }).await;
        assert_eq!(value, Ok("private-value"));
        let error = bcs_telemetry::observe_result("test.read", async { Err::<(), _>("private-password") }).await;
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
            bcs_telemetry::observe_result("test.cancel", std::future::pending::<Result<(), ()>>())).await;
        assert!(result.is_err());
    }).await;
    assert!(logs.contains("cancelled"));
    assert!(!logs.contains("\"outcome\":\"success\""));
}

#[tokio::test]
async fn spawned_work_keeps_request_correlation_and_counts_fallbacks() {
    let logs = capture(bcs_telemetry::with_request_context("request-42".into(), async {
        bcs_telemetry::count("test.cache", "miss");
        tokio::spawn(bcs_telemetry::in_current_context(async {
            assert_eq!(bcs_telemetry::current_request_id(), "request-42");
            let _ = bcs_telemetry::observe_result("test.read", async { Err::<(), _>(()) }).await;
            bcs_telemetry::observe_result("test.read", async { Ok::<_, ()>(()) }).await.unwrap();
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
    assert_eq!(bcs_telemetry::current_request_id(), "");
}

#[tokio::test(start_paused = true)]
async fn stalled_warning_does_not_timeout_or_repeat() {
    let logs = capture(async {
        let (tx, rx) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(bcs_telemetry::in_current_context(
            bcs_telemetry::observe_result("test.slow", rx)));
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
        let failed = bcs_telemetry::observe_result("test.non_send", async {
            Err::<(), Box<dyn std::error::Error>>("expected".into())
        }).await.is_err();
        tokio::task::yield_now().await;
        assert!(failed);
    }).await.unwrap();
}

#[tokio::test]
async fn nested_and_spawned_operations_report_the_parent_id() {
    let logs = capture(bcs_telemetry::observe_value("test.parent", async {
        let parent = bcs_telemetry::current_operation_id();
        assert!(!parent.is_empty());
        tokio::spawn(bcs_telemetry::in_current_context(async move {
            assert_eq!(bcs_telemetry::current_operation_id(), parent);
            bcs_telemetry::observe_value("test.child", async {}).await;
        })).await.unwrap();
    })).await;
    let events: Vec<serde_json::Value> = logs.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let parent = events.iter().find(|e| e["fields"]["operation"] == "test.parent").unwrap();
    let child = events.iter().find(|e| e["fields"]["operation"] == "test.child").unwrap();
    assert_eq!(child["fields"]["parent_operation_id"], parent["fields"]["operation_id"]);
    assert_eq!(bcs_telemetry::current_operation_id(), "");
}
