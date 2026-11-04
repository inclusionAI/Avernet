//! Real transport consumers, with a recording implementation of the plugin API.
use std::{convert::Infallible, sync::{Arc, Mutex}, time::Duration};
use axum::{Router, body::Body, extract::Query, http::{Request, StatusCode}, middleware, response::sse::{Event, Sse}, routing::get};
use bcs_tracing_api::{Carrier, Context, Kind, NetworkTracer, Outcome, Record, RequestInfo, StartedTrace, with_provider};
use tower::ServiceExt;

#[derive(Default)]
struct Spy(Mutex<Vec<Outcome>>);
struct Completion(Arc<Spy>);
impl Record for Completion {
    fn finish(self: Box<Self>, outcome: Outcome, _: Option<u64>) { self.0.0.lock().unwrap().push(outcome); }
}
struct Provider(Arc<Spy>);
impl NetworkTracer for Provider {
    fn start(&self, info: &RequestInfo<'_>, _: &Carrier, _: Option<&Context>) -> StartedTrace {
        assert_eq!(info.kind, Kind::HttpServer);
        assert!(!info.endpoint.contains('?'));
        StartedTrace { context: Arc::new(()), headers: vec![], record: Box::new(Completion(self.0.clone())) }
    }
}

#[tokio::test]
async fn middleware_records_auth_extraction_fallback_and_caught_panic() {
    let spy = Arc::new(Spy::default());
    let router = Router::new()
        .route("/auth", get(|| async { StatusCode::UNAUTHORIZED }))
        .route("/query", get(|_: Query<std::collections::HashMap<String, u32>>| async { "ok" }))
        .route("/panic", get(|| async { panic!("test panic"); #[allow(unreachable_code)] "unreachable" }))
        .layer(tower_http::catch_panic::CatchPanicLayer::new())
        .layer(middleware::from_fn(bcs_network_tracing::http_server));
    with_provider(Some(Arc::new(Provider(spy.clone()))), async {
        for (path, code) in [("/auth", 401), ("/query?a=invalid", 400), ("/missing", 404), ("/panic", 500)] {
            let response = router.clone().oneshot(Request::builder().uri(path).body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status().as_u16(), code);
        }
    }).await;
    assert_eq!(*spy.0.lock().unwrap(), vec![Outcome::Http(401), Outcome::Http(400), Outcome::Http(404), Outcome::Http(500)]);
}

#[tokio::test]
async fn response_ready_does_not_wait_for_sse_and_cancellation_finishes_once() {
    let spy = Arc::new(Spy::default());
    let router = Router::new()
        .route("/stream", get(|| async { Sse::new(futures::stream::pending::<Result<Event, Infallible>>()) }))
        .route("/pending", get(|| async { std::future::pending::<()>().await; "never" }))
        .layer(middleware::from_fn(bcs_network_tracing::http_server));
    with_provider(Some(Arc::new(Provider(spy.clone()))), async {
        let response = tokio::time::timeout(Duration::from_secs(1), router.clone().oneshot(
            Request::builder().uri("/stream").body(Body::empty()).unwrap())).await.unwrap().unwrap();
        assert_eq!(response.headers()["content-type"], "text/event-stream");
        drop(response);
        let result = tokio::time::timeout(Duration::from_millis(10), router.oneshot(
            Request::builder().uri("/pending").body(Body::empty()).unwrap())).await;
        assert!(result.is_err());
        assert!(bcs_tracing_api::current_context().is_none());
    }).await;
    assert_eq!(*spy.0.lock().unwrap(), vec![Outcome::Http(200), Outcome::Cancelled]);
}

#[tokio::test]
async fn no_provider_keeps_responses_unchanged() {
    let router = Router::new().route("/", get(|| async { ([("x-original", "kept")], "ok") }))
        .layer(middleware::from_fn(bcs_network_tracing::http_server));
    with_provider(None, async {
        let response = router.oneshot(Request::builder().uri("/").body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["x-original"], "kept");
        assert!(!response.headers().contains_key("sofatraceid"));
    }).await;
}
