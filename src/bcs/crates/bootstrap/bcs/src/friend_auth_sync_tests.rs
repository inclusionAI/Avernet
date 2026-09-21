//! Public FriendAuthSyncPort -> TC HTTP contract regression tests.
use super::*;
use axum::{routing::post, Json, Router};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use std::time::Duration;

fn command(bot_id: &str, action: FriendAuthSyncAction) -> FriendAuthSyncCommand {
    FriendAuthSyncCommand {
        env: "dev".to_string(),
        bot_id: bot_id.to_string(),
        // Deliberately different: the TC actor suffix is authoritative.
        owner_work_no: "legacy-owner".to_string(),
        human_work_no: "88123".to_string(),
        action,
        request_id: Some("request-1".to_string()),
        request_auth: None,
    }
}

async fn backend() -> (HttpFriendAuthSyncPort, mpsc::Receiver<Value>, tokio::task::JoinHandle<()>) {
    let (tx, rx) = mpsc::channel(8);
    let app = Router::new().route(SYNC_PATH, post(move |Json(body): Json<Value>| {
        let tx = tx.clone();
        async move {
            tx.send(body).await.unwrap();
            Json(json!({"synced": true, "reason": "created", "auth_id": 42}))
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let adapter = HttpFriendAuthSyncPort::new(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (adapter, rx, task)
}

#[tokio::test]
async fn tc_grant_sends_bare_bot_id_and_suffix_owner() {
    let (adapter, mut rx, task) = backend().await;
    adapter.sync(command("abc123:85020", FriendAuthSyncAction::Grant)).await.unwrap();
    let body = tokio::time::timeout(Duration::from_secs(2), rx.recv()).await.unwrap().unwrap();
    task.abort();
    assert_eq!(body, json!({
        "bot_id": "abc123", "owner_work_no": "85020", "human_work_no": "88123",
        "action": "grant", "request_id": "request-1"
    }));
}

#[tokio::test]
async fn tc_revoke_sends_the_same_identity_without_request_id() {
    let (adapter, mut rx, task) = backend().await;
    let mut revoke = command("abc123:85020", FriendAuthSyncAction::Revoke);
    revoke.request_id = None;
    // A missing legacy created_by must not prevent TC address resolution.
    revoke.owner_work_no.clear();
    adapter.sync(revoke).await.unwrap();
    let body = tokio::time::timeout(Duration::from_secs(2), rx.recv()).await.unwrap().unwrap();
    task.abort();
    assert_eq!(body, json!({
        "bot_id": "abc123", "owner_work_no": "85020", "human_work_no": "88123",
        "action": "revoke"
    }));
}

#[tokio::test]
async fn non_tc_and_malformed_targets_never_reach_backend() {
    let (adapter, mut rx, task) = backend().await;
    for actor in ["native-bot-uuid", "", ":85020", "bot-1:", "bot:85020:extra", "bot: ", " bot:85020"] {
        for action in [FriendAuthSyncAction::Grant, FriendAuthSyncAction::Revoke] {
            adapter.sync(command(actor, action)).await.unwrap();
        }
    }
    assert!(tokio::time::timeout(Duration::from_millis(50), rx.recv()).await.is_err());
    task.abort();
}

#[tokio::test]
async fn backend_errors_and_negative_acknowledgements_are_not_success() {
    use axum::http::StatusCode;
    for (status, body) in [
        (StatusCode::BAD_GATEWAY, json!({"error": "sync failed"})),
        (StatusCode::UNAUTHORIZED, json!({"error": "unauthorized"})),
        (StatusCode::OK, json!({"synced": false})),
        (StatusCode::OK, json!({"unexpected": "response"})),
    ] {
        let app = Router::new().route(SYNC_PATH, post(move || {
            let body = body.clone();
            async move { (status, Json(body)) }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let adapter = HttpFriendAuthSyncPort::new(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
        let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let result = adapter.sync(command("abc123:85020", FriendAuthSyncAction::Revoke)).await;
        task.abort();
        assert!(result.is_err(), "status={status} must not be reported as synced");
    }
}
