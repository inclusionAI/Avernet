//! Real transport consumers, with a recording implementation of the plugin API.
// Fixture construction and assertions deliberately fail the test on error.
#![allow(clippy::unwrap_used)]
use std::{convert::Infallible, sync::{Arc, Mutex}, time::Duration};
use axum::{Router, body::Body, extract::Query, http::{Request, StatusCode}, middleware, response::sse::{Event, Sse}, routing::get};
use bcs_tracing_api::{Carrier, Kind, NetworkTracer, Outcome, Record, RequestInfo, StartedTrace, with_provider};
use tower::ServiceExt;

#[derive(Default)]
struct Spy(Mutex<Vec<Outcome>>);
struct Completion(Arc<Spy>);
impl Record for Completion {
    fn finish(self: Box<Self>, outcome: Outcome, _: Option<u64>) { self.0.0.lock().unwrap().push(outcome); }
}
struct Provider(Arc<Spy>);
impl NetworkTracer for Provider {
    fn start(&self, info: &RequestInfo<'_>, _: &Carrier, _: bcs_tracing_api::ParentContext<'_>) -> StartedTrace {
        assert_eq!(info.kind, Kind::HttpServer);
        assert!(!info.endpoint.contains('?'));
        StartedTrace { remove_headers: vec![], context: Arc::new(()), headers: vec![], record: Box::new(Completion(self.0.clone())) }
    }
}

#[tokio::test]
async fn middleware_records_business_errors_and_skips_unmatched_routes() {
    let spy = Arc::new(Spy::default());
    let router = Router::new()
        .route("/auth", get(|| async { StatusCode::UNAUTHORIZED }))
        .route("/not-found", get(|| async { StatusCode::NOT_FOUND }))
        .route("/query", get(|_: Query<std::collections::HashMap<String, u32>>| async { "ok" }))
        .route("/panic", get(|| async { panic!("test panic"); #[allow(unreachable_code)] "unreachable" }))
        .layer(tower_http::catch_panic::CatchPanicLayer::new())
        .layer(middleware::from_fn_with_state(bcs_network_tracing::HttpServerFilter::default(), bcs_network_tracing::http_server));
    with_provider(Some(Arc::new(Provider(spy.clone()))), async {
        for (path, code) in [("/auth", 401), ("/query?a=invalid", 400), ("/not-found", 404), ("/missing", 404), ("/panic", 500)] {
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
        .layer(middleware::from_fn_with_state(bcs_network_tracing::HttpServerFilter::default(), bcs_network_tracing::http_server));
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
        .layer(middleware::from_fn_with_state(bcs_network_tracing::HttpServerFilter::default(), bcs_network_tracing::http_server));
    with_provider(None, async {
        let response = router.oneshot(Request::builder().uri("/").body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["x-original"], "kept");
        assert!(!response.headers().contains_key("sofatraceid"));
    }).await;
}

#[tokio::test]
async fn excluded_routes_and_preflights_suppress_the_entire_trace_scope() {
    use axum::http::Method;
    use bcs_network_tracing::HttpServerFilter;
    use bcs_tracing_api::{Call, with_context};

    let spy = Arc::new(Spy::default());
    let filter = HttpServerFilter::new([
        "/health", "/internal/metrics", "/manifest", "/assets/{bundle}/{file}",
        "/api/v1/collaboration/manifest", "/api/v1/collaboration/assets/{bundle}/{file}",
        "/ws", "/ws/bot", "/openapi/v1/collaboration/messages/ws",
    ].map(str::to_owned));
    async fn excluded() -> &'static str {
        assert!(!bcs_tracing_api::is_enabled());
        assert!(bcs_tracing_api::current_context().is_none());
        // Even a client instrumented for a business call must remain disabled
        // when it is invoked from a probe. Provider would reject HttpClient.
        let call = Call::start(&RequestInfo { kind: Kind::HttpClient, method: "GET",
            endpoint: "/dependency", route: None, target_app: "", request_size: None }, &vec![]);
        assert!(call.headers().is_empty());
        call.finish(Outcome::Http(200), None);
        "unchanged"
    }
    let mut router = Router::new()
        .route("/bots/status", axum::routing::post(|| async {
            assert!(bcs_tracing_api::current_context().is_some());
            StatusCode::FORBIDDEN
        }))
        .route("/health-report", get(|| async { StatusCode::OK }))
        .fallback(excluded);
    for path in ["/health", "/internal/metrics", "/manifest", "/assets/{bundle}/{file}",
        "/ws", "/ws/bot", "/openapi/v1/collaboration/messages/ws"] {
        router = router.route(path, get(excluded));
    }
    let router = router
        .nest("/api/v1/collaboration", Router::new()
            .route("/manifest", get(excluded))
            .route("/assets/{bundle}/{file}", get(excluded)))
        .layer(tower_http::cors::CorsLayer::permissive())
        .layer(middleware::from_fn_with_state(filter, bcs_network_tracing::http_server));
    with_provider(Some(Arc::new(Provider(spy.clone()))), with_context(Some(Arc::new(())), async {
        for path in ["/health?probe=1", "/internal/metrics", "/manifest", "/assets/ui/app.js",
            "/api/v1/collaboration/manifest", "/api/v1/collaboration/assets/ui/app.js",
            "/ws", "/ws/bot", "/openapi/v1/collaboration/messages/ws", "/unmatched"] {
            let response = router.clone().oneshot(Request::builder().uri(path)
                .header("sofatraceid", "upstream").header("sofarpcid", "0.1")
                .body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            assert_eq!(axum::body::to_bytes(response.into_body(), 100).await.unwrap(), "unchanged");
            assert!(bcs_tracing_api::is_enabled());
            assert!(bcs_tracing_api::current_context().is_some());
        }
        let preflight = router.clone().oneshot(Request::builder().method(Method::OPTIONS)
            .uri("/bots/status").header("origin", "https://example.test")
            .header("access-control-request-method", "POST").body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(preflight.status(), StatusCode::OK);
        assert!(spy.0.lock().unwrap().is_empty());
        for (method, path, expected) in [(Method::POST, "/bots/status", 403), (Method::GET, "/health-report", 200)] {
            let response = router.clone().oneshot(Request::builder().method(method).uri(path)
                .body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status().as_u16(), expected);
        }
    })).await;
    assert_eq!(*spy.0.lock().unwrap(), vec![Outcome::Http(403), Outcome::Http(200)]);
}
