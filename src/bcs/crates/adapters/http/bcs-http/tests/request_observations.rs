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

#[tokio::test]
async fn detached_logging_does_not_extend_existing_a2a_spans() {
    use opentelemetry::{global, trace::TracerProvider as _};
    use opentelemetry_sdk::{
        propagation::TraceContextPropagator,
        trace::{InMemorySpanExporterBuilder, SdkTracerProvider},
    };
    use tower_http::trace::MakeSpan;
    use tracing::Instrument;
    use tracing_subscriber::prelude::*;

    global::set_text_map_propagator(TraceContextPropagator::new());
    for (path, span_name) in [
        ("/bots/bot-1/chat-async", "bcn.gateway.dispatch"),
        ("/bot/events", "bcn.bot.response"),
    ] {
        let exporter = InMemorySpanExporterBuilder::new().build();
        let provider = SdkTracerProvider::builder().with_simple_exporter(exporter.clone()).build();
        let buffer = Buffer::default();
        let writer = buffer.clone();
        let subscriber = tracing_subscriber::registry()
            .with(tracing_subscriber::fmt::layer().json().with_writer(move || writer.clone()))
            .with(tracing_opentelemetry::layer().with_tracer(provider.tracer("detached-logging-test")));
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let (release_tx, release_rx) = tokio::sync::oneshot::channel();
        let task = async {
            let request = Request::builder().method("POST").uri(path)
                .header("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
                .body(()).unwrap();
            let span = bcs_http::gateway_trace::BcnMakeSpan.make_span(&request);
            bcs_telemetry::with_request_context("detached-request".into(),
                bcs_telemetry::observe_value("test.request", async move {
                    tokio::spawn(bcs_telemetry::in_current_context(async move {
                        started_tx.send(()).unwrap();
                        release_rx.await.unwrap();
                        let ids = (bcs_telemetry::current_request_id(), bcs_telemetry::current_trace_id());
                        let has_active_span = tracing::Span::current().id().is_some();
                        bcs_telemetry::observe_value("test.background", async {}).await;
                        (ids, has_active_span)
                    }))
                }).instrument(span)).await
        }.with_subscriber(subscriber).await;
        started_rx.await.unwrap();
        assert!(!task.is_finished());
        provider.force_flush().unwrap();
        let spans_before_background_finishes = exporter.get_finished_spans().unwrap();
        release_tx.send(()).unwrap();
        let ((request_id, trace_id), has_active_span) = task.await.unwrap();
        provider.force_flush().unwrap();

        assert_eq!(spans_before_background_finishes.len(), 1,
            "{span_name} must close when request work ends, while background work is still waiting");
        assert_eq!(spans_before_background_finishes[0].name, span_name);
        assert_eq!(exporter.get_finished_spans().unwrap().len(), 1);
        assert!(!has_active_span, "log correlation must not enter the original A2A span");
        assert_eq!(request_id, "detached-request");
        assert_eq!(trace_id, "0af7651916cd43dd8448eb211c80319c");
        let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
        let background = logs.lines().map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
            .find(|event| event["fields"]["operation"] == "test.background").unwrap();
        assert_eq!(background["fields"]["request_id"], request_id);
        assert_eq!(background["fields"]["trace_id"], trace_id);
    }
}
