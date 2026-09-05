use axum::{body::Body, extract::Json, http::{Request, StatusCode}, middleware, routing::post, Router};
use std::io::Write;
use std::sync::{Arc, Mutex};
use tower::ServiceExt;
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
fn app() -> Router {
    Router::new().route("/bots/query", post(|Json(_): Json<serde_json::Value>| async { StatusCode::OK }))
        .route("/slow", post(|| async { std::future::pending::<StatusCode>().await }))
        .layer(middleware::from_fn(bcs_http::gateway_trace::observe_request))
}

#[tokio::test]
async fn malformed_body_is_correlated_before_handler_and_payload_is_not_logged() {
    let buffer = Buffer::default();
    let writer = buffer.clone();
    let subscriber = tracing_subscriber::fmt().json()
        .with_span_events(tracing_subscriber::fmt::format::FmtSpan::NEW)
        .with_writer(move || writer.clone()).finish();
    async {
        let request = Request::builder().method("POST").uri("/bots/query?secret=private-query")
            .header("content-type", "application/json").header("x-request-id", "request-42")
            .body(Body::from("private-body")).unwrap();
        let response = app().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(response.headers()["x-request-id"], "request-42");
    }.with_subscriber(subscriber).await;
    let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    assert!(logs.contains("http.request.started"));
    assert!(logs.contains("http.request.response_ready"));
    assert!(logs.contains("http.request.operations"));
    assert!(logs.contains("request-42"));
    assert!(!logs.contains("private-"));
    for line in logs.lines() {
        let event: serde_json::Value = serde_json::from_str(line).unwrap();
        assert!(event.get("span").is_none() && event.get("spans").is_none(),
            "request logging must not create spans: {line}");
    }
}

#[tokio::test]
async fn unsafe_or_oversized_request_id_is_replaced_with_safe_id() {
    for supplied in ["bad id".to_string(), "x".repeat(129)] {
        let response = app().oneshot(Request::builder().method("POST").uri("/bots/query")
            .header("content-type", "application/json").header("x-request-id", supplied.as_str())
            .body(Body::from("{}")).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let id = response.headers()["x-request-id"].to_str().unwrap();
        assert_ne!(id, supplied);
        assert!(uuid::Uuid::parse_str(id).is_ok());
    }
}

#[tokio::test]
async fn cancelled_handler_emits_termination_without_success() {
    let buffer = Buffer::default();
    let writer = buffer.clone();
    let subscriber = tracing_subscriber::fmt().json().with_writer(move || writer.clone()).finish();
    async {
        let request = Request::builder().method("POST").uri("/slow")
            .header("x-request-id", "cancelled-42").body(Body::empty()).unwrap();
        assert!(tokio::time::timeout(std::time::Duration::from_millis(5), app().oneshot(request)).await.is_err());
    }.with_subscriber(subscriber).await;
    let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    assert!(logs.contains("http.request.cancelled"));
    assert!(logs.contains("cancelled-42"));
    assert!(!logs.contains("http.request.response_ready"));
}
