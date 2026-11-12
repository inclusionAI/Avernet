use super::*;
use axum::{Router, body::Body, http::{Request, StatusCode}, middleware, response::IntoResponse, routing::post};
use std::{io::Write, sync::{Arc, Mutex}};
use tower::ServiceExt;
use tracing::instrument::WithSubscriber;

#[tokio::test]
async fn nonterminal_success_is_not_selected_but_errors_and_terminal_are() {
    #[derive(Clone, Default)]
    struct Buffer(Arc<Mutex<Vec<u8>>>);
    impl Write for Buffer {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> { self.0.lock().unwrap().write(bytes) }
        fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
    }
    let buffer = Buffer::default(); let output = buffer.clone();
    let subscriber = tracing_subscriber::fmt().json().with_writer(move || output.clone()).finish();
    let app = Router::new().route("/delta", post(|| async {
        let mut response = StatusCode::OK.into_response();
        response.extensions_mut().insert(NonTerminalCallback);
        response
    })).route("/final", post(|| async { StatusCode::OK }))
        .route("/failed", post(|| async {
            let mut response = StatusCode::BAD_REQUEST.into_response();
            response.extensions_mut().insert(NonTerminalCallback);
            response
        })).layer(middleware::from_fn(observe_request));
    async {
        for path in std::iter::repeat_n("/delta", 1000).chain(["/final", "/failed"]) {
            let response = app.clone().oneshot(Request::post(path).body(Body::empty()).unwrap()).await.unwrap();
            assert!(response.headers().contains_key("x-request-id"));
        }
    }.with_subscriber(subscriber).await;
    let text = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    let rows: Vec<serde_json::Value> = text.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    let selected: Vec<_> = rows.iter().filter(|r| r["fields"]["log_class"] == "business_key").collect();
    assert_eq!(selected.len(), 2);
    assert_eq!(selected[0]["fields"]["route"], "/final");
    assert_eq!(selected[1]["fields"]["route"], "/failed");
    assert_eq!(rows.iter().filter(|r| r["fields"]["message"] == "http.request.response_ready").count(), 1002);
}

#[test]
fn aborted_request_emits_captured_log_identity_after_scope_exit() {
    use tracing_subscriber::{Layer, prelude::*};
    #[derive(Clone, Default)]
    struct Capture(Arc<Mutex<Vec<Option<bcs_tracing_api::LogCorrelation>>>>);
    impl<S: tracing::Subscriber> Layer<S> for Capture {
        fn on_event(&self, event: &tracing::Event<'_>, _: tracing_subscriber::layer::Context<'_, S>) {
            struct Cancelled(bool);
            impl tracing::field::Visit for Cancelled {
                fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
                    if field.name() == "event_code" && value == "http.request.cancelled" { self.0 = true; }
                }
                fn record_debug(&mut self, _: &tracing::field::Field, _: &dyn std::fmt::Debug) {}
            }
            let mut cancelled = Cancelled(false); event.record(&mut cancelled);
            if cancelled.0 { self.0.lock().unwrap().push(bcs_tracing_api::current_log_correlation()); }
        }
    }
    let entered = Arc::new(tokio::sync::Notify::new()); let notify = entered.clone();
    let app = Router::new().route("/cancel", post(move || { let notify = notify.clone(); async move {
        notify.notify_one(); std::future::pending::<StatusCode>().await
    }})).layer(middleware::from_fn(observe_request));
    let identity = bcs_tracing_api::LogCorrelation::new("network_trace", "cancelled-request", "network_span", "0.7").unwrap();
    let capture = Capture::default(); let output = capture.clone();
    let future = bcs_tracing_api::with_log_correlation(Some(identity.clone()), || {
        bcs_tracing_api::in_current_log_context(app.oneshot(Request::post("/cancel").body(Body::empty()).unwrap()))
    });
    // Production logging is process-wide and outlives request cancellation.
    // WithSubscriber only restores a task's dispatch while polling, not on Drop.
    tracing::subscriber::with_default(tracing_subscriber::registry().with(capture), || {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let task = tokio::spawn(future);
            entered.notified().await; task.abort();
            assert!(task.await.unwrap_err().is_cancelled());
        });
    });
    assert_eq!(*output.0.lock().unwrap(), vec![Some(identity)]);
    assert!(bcs_tracing_api::current_log_correlation().is_none());
}
