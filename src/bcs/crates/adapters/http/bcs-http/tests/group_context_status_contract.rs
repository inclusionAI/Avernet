//! Contract test for `POST /groupcontext/status`.
//!
//! Verifies that:
//! 1. The route returns 200 with empty contexts/templates when the noop repo is wired.
//! 2. The `actor_id` is correctly resolved from the auth header (StaticAuthPlugin
//!    injects a `bot_uuid` into the principal).
//! 3. A request without auth returns 401.

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthConfig, AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_group_context::{GroupContextApplication, GroupContextCore, NoopGroupContextRepo};
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tower::ServiceExt;

fn test_router_with_bot(bot_uuid: &str) -> axum::Router {
    let repo = Arc::new(NoopGroupContextRepo);
    let core = Arc::new(GroupContextCore::new(repo));
    let app = Arc::new(GroupContextApplication::new(core));

    let principal = AuthPrincipal {
        bot_uuid: Some(bot_uuid.to_string()),
        ..Default::default()
    };
    let auth_chain = Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]));
    let state = HttpAppState::new(Services::builder().build_for_test())
        .with_group_context_application(app)
        .with_auth_chain(auth_chain.clone(), AuthConfig::default())
        .with_user_identity(Arc::new(ChainUserIdentityPort::new(auth_chain)));
    build_router(state)
}

#[tokio::test]
async fn status_returns_empty_lists_for_authenticated_bot() {
    let router = test_router_with_bot("test-bot-001");

    let response = router
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groupcontext/status")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "tenant_id": "tenant-1",
                        "group_id": "group-1",
                        "domain": "game_rule"
                    })
                    .to_string(),
                ))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);

    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body");
    let json: Value = serde_json::from_slice(&body).expect("response JSON");

    assert_eq!(json["contexts"].as_array().unwrap().len(), 0);
    assert_eq!(json["templates"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn status_rejects_unauthenticated_request() {
    // Build router WITHOUT auth chain — bot_uuid_from_headers returns None.
    let repo = Arc::new(NoopGroupContextRepo);
    let core = Arc::new(GroupContextCore::new(repo));
    let app = Arc::new(GroupContextApplication::new(core));

    let state = HttpAppState::new(Services::builder().build_for_test())
        .with_group_context_application(app);
    let router = build_router(state);

    let response = router
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groupcontext/status")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "tenant_id": "tenant-1",
                        "group_id": "group-1"
                    })
                    .to_string(),
                ))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn status_with_session_id_and_run_id() {
    let router = test_router_with_bot("session-bot");

    let response = router
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groupcontext/status")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "tenant_id": "tenant-1",
                        "group_id": "group-1",
                        "session_id": "sess-abc",
                        "run_id": "run-xyz",
                        "limit": 10
                    })
                    .to_string(),
                ))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);

    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body");
    let json: Value = serde_json::from_slice(&body).expect("response JSON");

    assert_eq!(json["contexts"].as_array().unwrap().len(), 0);
    assert_eq!(json["templates"].as_array().unwrap().len(), 0);
}