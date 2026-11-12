//! Legacy error-mapping branches of `POST /groups/{id}/sessions`.
//!
//! The route translates every `SessionLaunchError` variant into a documented
//! HTTP status; these tests pin the mapping and the legacy request-shape
//! translation (`caller_role`, `session_kind`).

use axum::http::StatusCode;
use bcs_service_api::GroupStrategy;
use serde_json::json;

use super::{builder, call, env, json_as, LaunchFailure, ScriptedLaunch};

const PATH: &str = "/groups/group-1/sessions";

fn create_request(body: serde_json::Value) -> axum::http::Request<axum::body::Body> {
    json_as("POST", PATH, "driver-token", body)
}

#[tokio::test]
async fn create_session_rejects_unauthenticated_caller() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        super::json_body("POST", PATH, json!({"session_title": "x"})),
    )
    .await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"], "unauthorized");
}

#[tokio::test]
async fn create_session_maps_group_not_found() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::GroupNotFound))
        .build();

    let (status, body) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["message"], "group group-1 not found");
}

#[tokio::test]
async fn create_session_maps_missing_session_on_reactivation() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::SessionNotFound))
        .build();

    let (status, body) = call(
        &env.app,
        create_request(json!({"session_id": "group-1:deadbeef"})),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "group-1:deadbeef");
}

#[tokio::test]
async fn create_session_maps_forbidden() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::Forbidden))
        .build();

    let (status, body) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["message"], "not your group");
}

#[tokio::test]
async fn create_session_maps_invalid_role() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::InvalidRole))
        .build();

    let (status, body) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "invalid_role");
    assert_eq!(body["message"], "bad role");
}

#[tokio::test]
async fn create_session_maps_invalid_request() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::InvalidRequest))
        .build();

    let (status, body) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "bad request");
}

#[tokio::test]
async fn create_session_maps_conflict() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::Conflict))
        .build();

    let (status, body) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "already running");
}

#[tokio::test]
async fn create_session_maps_runtime_error() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::Runtime))
        .build();

    let (status, body) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "invalid_request");
}

#[tokio::test]
async fn create_session_maps_internal_error() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .launch(ScriptedLaunch::failing(LaunchFailure::Internal))
        .build();

    let (status, body) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(body["error"], "Internal error: launch store down");
}

#[tokio::test]
async fn create_session_returns_created_status() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(&env.app, create_request(json!({"session_title": "New"}))).await;

    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body["session_id"], "group-1:00000001");
}

#[tokio::test]
async fn create_session_returns_ok_when_not_created() {
    let env = builder(GroupStrategy::Chat, None).await.build();
    *env.launch.created.lock().await = false;

    let (status, _) = call(&env.app, create_request(json!({}))).await;

    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn create_session_accepts_every_legacy_creator_role() {
    let env = env(GroupStrategy::Chat, None).await;

    for role in ["driver", "consultant", "manager", "worker", "observer", "intern"] {
        let (status, _) = call(
            &env.app,
            create_request(json!({"caller_role": role, "session_title": role})),
        )
        .await;
        assert_eq!(status, StatusCode::CREATED, "role {role}");
    }
}

#[tokio::test]
async fn create_session_treats_unknown_kind_as_chat() {
    let env = env(GroupStrategy::Chat, None).await;

    for kind in ["service_invocation", "unknown_kind"] {
        let (status, _) = call(
            &env.app,
            create_request(json!({"session_kind": kind, "session_title": kind})),
        )
        .await;
        assert_eq!(status, StatusCode::CREATED, "kind {kind}");
    }
}

#[tokio::test]
async fn create_session_uses_a_human_caller_identity() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, _) = call(
        &env.app,
        super::json_body("POST", PATH, json!({"session_title": "Human"})),
    )
    .await;

    assert_eq!(status, StatusCode::CREATED);
}
