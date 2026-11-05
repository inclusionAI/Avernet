//! Route templates are supplied by Axum, independently of concrete URL paths.
#![allow(clippy::unwrap_used)]
use std::sync::{Arc, Mutex};
use axum::{Router, body::Body, http::Request, middleware, routing::post};
use bcs_tracing_api::{Carrier, NetworkTracer, Outcome, ParentContext, Record, RequestInfo, StartedTrace, with_provider};
use tower::ServiceExt;

#[derive(Default)]
struct Provider(Mutex<Vec<(String, Option<String>)>>);
struct Completion;
impl Record for Completion {
    fn finish(self: Box<Self>, _: Outcome, _: Option<u64>) {}
}
impl NetworkTracer for Provider {
    fn start(&self, info: &RequestInfo<'_>, _: &Carrier, _: ParentContext<'_>) -> StartedTrace {
        self.0.lock().unwrap().push((info.endpoint.into(), info.route.map(str::to_owned)));
        StartedTrace { remove_headers: vec![], context: Arc::new(()), headers: vec![], record: Box::new(Completion) }
    }
}

#[tokio::test]
async fn nested_routes_share_templates_and_exclusions_do_not_record() {
    let provider = Arc::new(Provider::default());
    let router = Router::new().nest("/api/v1", Router::new()
        .route("/bots/{id}/chat", post(|| async { "ok" }))
        .route("/health/{probe}", post(|| async { "ok" })))
        .layer(middleware::from_fn_with_state(
            bcs_network_tracing::HttpServerFilter::new(["/api/v1/health/{probe}".into()]),
            bcs_network_tracing::http_server));
    with_provider(Some(provider.clone()), async {
        for path in ["/api/v1/bots/one/chat?secret=hidden", "/api/v1/bots/two/chat", "/api/v1/health/db", "/missing"] {
            router.clone().oneshot(Request::post(path).body(Body::empty()).unwrap()).await.unwrap();
        }
    }).await;
    assert_eq!(*provider.0.lock().unwrap(), vec![
        ("/api/v1/bots/one/chat".into(), Some("/api/v1/bots/{id}/chat".into())),
        ("/api/v1/bots/two/chat".into(), Some("/api/v1/bots/{id}/chat".into())),
    ]);
}
