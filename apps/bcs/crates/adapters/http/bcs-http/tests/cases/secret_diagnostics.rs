use std::{net::SocketAddr, sync::{Arc, atomic::{AtomicUsize, Ordering}}};

use axum::{body::{Body, to_bytes}, extract::ConnectInfo, http::{Request, StatusCode}};
use bcs_http::{gateway_trace::observe_request, router::build_router, state::HttpAppState};
use bcs_service_api::application::{SecretService, SecretServiceError, SecretView};
use bcs_services_container::Services;
use bcs_test_support::capture_request_logs;
use tower::ServiceExt;

struct UnavailableSecret(AtomicUsize);

#[async_trait::async_trait]
impl SecretService for UnavailableSecret {
    async fn get_secret(&self, name: &str) -> Result<SecretView, SecretServiceError> {
        assert_eq!(name, "diagnostic-key");
        self.0.fetch_add(1, Ordering::SeqCst);
        Err(SecretServiceError::Unavailable("secret backend offline".into()))
    }
}

#[tokio::test]
async fn secret_rejections_and_dependency_failures_keep_the_http_request_id() {
    let service = Arc::new(UnavailableSecret(AtomicUsize::new(0)));
    let state = HttpAppState::new(Services::builder().secret(service.clone()).build_for_test());
    let app = build_router(state).layer(axum::middleware::from_fn(observe_request));
    for (peer, id, status, kind, message, calls) in [
        ("192.0.2.1:10001", "secret-remote", StatusCode::FORBIDDEN, "loopback_only",
            "rejecting /admin/secret request from non-loopback", 0),
        ("127.0.0.1:10002", "secret-unavailable", StatusCode::SERVICE_UNAVAILABLE, "unavailable",
            "SecretService.get_secret failed", 1),
    ] {
        let request = Request::builder().uri("/admin/secret/diagnostic-key")
            .header("x-request-id", id)
            .extension(ConnectInfo(peer.parse::<SocketAddr>().unwrap()))
            .body(Body::empty()).unwrap();
        let (response, events) = capture_request_logs("outer-test-context", app.clone().oneshot(request)).await;
        let response = response.expect("HTTP response");
        assert_eq!(response.status(), status);
        assert_eq!(response.headers()["x-request-id"], id);
        let body: serde_json::Value = serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap()).unwrap();
        assert_eq!(body["error"], kind);
        assert_eq!(service.0.load(Ordering::SeqCst), calls);
        let warning = events.iter().find(|event| event["fields"]["message"] == message)
            .unwrap_or_else(|| panic!("missing diagnostic: {events:?}"));
        assert_eq!(warning["level"], "WARN");
        assert_eq!(warning["fields"]["request_id"], id);
        assert!(events.iter().all(|event| event["fields"].get("trace_id").is_none()));
    }
}
