use std::sync::{Arc, Mutex};
use axum::{body::Body, http::{Method, Request}};
use bcs_tracing_api::{Carrier, NetworkTracer, Outcome, ParentContext, Record, RequestInfo, StartedTrace};
use tower::ServiceExt;

#[derive(Default)]
struct Recorder(Mutex<Vec<String>>);
struct Completion;
impl Record for Completion {
    fn finish(self: Box<Self>, _: Outcome, _: Option<u64>) {}
}
impl NetworkTracer for Recorder {
    fn start(&self, info: &RequestInfo<'_>, _: &Carrier, _: ParentContext<'_>) -> StartedTrace {
        self.0.lock().unwrap().push(info.route.unwrap_or(info.endpoint).into());
        StartedTrace { context: Arc::new(()), headers: vec![], remove_headers: vec![], record: Box::new(Completion) }
    }
}

#[tokio::test]
async fn diagnostics_and_retired_routes_are_excluded_from_business_tracing() {
    let directory = tempfile::tempdir().unwrap();
    let config = crate::BcsConfig { bots_base_dir: directory.path().into(), ..Default::default() };
    let server = crate::BcsServer::new_allowing_private_outbound_for_tests(config);
    let router = server.build_router().await.unwrap();
    let recorder = Arc::new(Recorder::default());
    bcs_tracing_api::with_provider(Some(recorder.clone()), async {
        for (method, path) in [
            (Method::GET, "/me/repair-info"),
            (Method::GET, "/admin/secret/test-fixture"),
            (Method::PATCH, "/groups/test-fixture/settings"),
            (Method::GET, "/health"),
            (Method::GET, "/bots/test-fixture"),
            (Method::GET, "/me"),
        ] {
            let request = Request::builder().method(method).uri(path).body(Body::empty()).unwrap();
            let response = router.clone().oneshot(request).await.unwrap();
            assert_ne!(response.status(), axum::http::StatusCode::METHOD_NOT_ALLOWED);
        }
    }).await;
    assert_eq!(*recorder.0.lock().unwrap(), vec!["/bots/{id}", "/me"]);
}
