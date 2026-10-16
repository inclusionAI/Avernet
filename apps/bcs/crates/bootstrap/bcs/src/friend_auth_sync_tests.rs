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

// --- principal re-address contract -------------------------------------------
//
// The work-order approval callback re-addresses the approver's principal to
// BCN with iss=backend, and the backend's ordinary-HTTP seam only trusts
// issuers {"gateway", "bcs"}. A sync call that echoes the inbound credential
// verbatim therefore fails with 401; with a re-signer configured the port must
// hand the backend a token signed by BCN itself (iss=bcs, aud=backend).

use bcs_api_http::v1::gateway_principal::{GatewayPrincipalReSigner, GatewayPrincipalTrust};
use jsonwebtoken::{Algorithm, DecodingKey, EncodingKey, Header, Validation};
use std::time::{SystemTime, UNIX_EPOCH};

const RESIGNING_KEY: &str = "friend-auth-sync-resigner-test-key-0123";

fn now_unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

fn mint_principal_jwt(signing_key: &str, iss: &str, principals: serde_json::Value) -> String {
    let now = now_unix();
    let mut header = Header::new(Algorithm::HS256);
    header.kid = Some("bare".to_string());
    jsonwebtoken::encode(
        &header,
        &json!({
            "iss": iss, "aud": "bcs", "iat": now, "exp": now + 3600,
            "principals": principals,
        }),
        &EncodingKey::from_secret(signing_key.as_bytes()),
    )
    .unwrap()
}

fn approver_principals() -> serde_json::Value {
    json!([{
        "type": "user",
        "subject": {"id": "152819", "username": "approver"}
    }])
}

fn resigner() -> GatewayPrincipalReSigner {
    // Mirrors bootstrap's default trust: issuers gateway+backend, aud=bcs.
    GatewayPrincipalReSigner::new(
        RESIGNING_KEY.as_bytes(),
        GatewayPrincipalTrust::new(
            vec!["gateway".to_string(), "backend".to_string()],
            "bcs",
            "bare",
        )
        .unwrap(),
    )
    .unwrap()
}

fn authed_command(bot_id: &str, principal: String) -> FriendAuthSyncCommand {
    let mut cmd = command(bot_id, FriendAuthSyncAction::Grant);
    cmd.request_auth = Some(bcs_service_api::RequestAuthHeaders {
        authorization: Some("Bearer legacy".to_string()),
        cookie: None,
        forwarded_headers: vec![("x-avernet-principal".to_string(), principal)],
    });
    cmd
}

/// Stub backend that reports the (optional) forwarded x-avernet-principal.
#[allow(clippy::type_complexity)]
async fn backend_reporting_principal()
-> (HttpFriendAuthSyncPort, mpsc::Receiver<Option<String>>, tokio::task::JoinHandle<()>) {
    let (tx, rx) = mpsc::channel(8);
    let app = Router::new().route(SYNC_PATH, post(move |headers: axum::http::HeaderMap| {
        let tx = tx.clone();
        async move {
            let principal = headers
                .get("x-avernet-principal")
                .and_then(|value| value.to_str().ok())
                .map(str::to_string);
            let _ = tx.send(principal).await;
            Json(json!({"synced": true}))
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let adapter = HttpFriendAuthSyncPort::new(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (adapter, rx, task)
}

#[tokio::test]
async fn work_order_callback_principal_is_readdressed_to_bcs_for_backend() {
    let (adapter, mut rx, task) = backend_reporting_principal().await;
    let adapter = adapter.with_principal_resigner(resigner());
    let inbound = mint_principal_jwt(RESIGNING_KEY, "backend", approver_principals());
    adapter.sync(authed_command("abc123:85020", inbound)).await.unwrap();
    let forwarded =
        tokio::time::timeout(Duration::from_secs(2), rx.recv()).await.unwrap().unwrap().unwrap();
    task.abort();

    // Verify the forwarded token the way the backend's seam does: shared key,
    // iss within its ordinary-HTTP allow-list, aud addressed to itself.
    let mut validation = Validation::new(Algorithm::HS256);
    validation.set_issuer(&["gateway", "bcs"]);
    validation.set_audience(&["backend"]);
    let data = jsonwebtoken::decode::<serde_json::Value>(
        forwarded.as_str(),
        &DecodingKey::from_secret(RESIGNING_KEY.as_bytes()),
        &validation,
    )
    .unwrap();
    assert_eq!(data.header.kid.as_deref(), Some("bare"));
    assert_eq!(data.claims["iss"], "bcs");
    assert_eq!(data.claims["aud"], "backend");
    // The approver identity rides along untouched.
    assert_eq!(data.claims["principals"], approver_principals());
    let iat = data.claims["iat"].as_u64().unwrap();
    let exp = data.claims["exp"].as_u64().unwrap();
    assert!(exp > now_unix(), "re-addressed token must not be expired");
    assert!(iat <= now_unix() + 5, "iat must be refreshed, not copied");
    assert_eq!(exp - iat, 60, "re-addressed principal must be short-lived");
}

#[tokio::test]
async fn unverifiable_callback_principal_fails_closed_before_sending() {
    let (adapter, mut rx, task) = backend_reporting_principal().await;
    let adapter = adapter.with_principal_resigner(resigner());
    let forged = mint_principal_jwt(
        "an-attacker-controlled-signing-key-012345",
        "backend",
        approver_principals(),
    );
    let result = adapter.sync(authed_command("abc123:85020", forged)).await;
    task.abort();
    match result {
        Err(bcs_service_api::ServiceError::InternalError(message)) => {
            assert!(
                message.contains("x-avernet-principal"),
                "failure must name the credential, got: {message}"
            );
        }
        other => panic!("unverifiable principal must fail closed, got: {other:?}"),
    }
    // Nothing may reach the backend: forwarding a credential known to be bad
    // is a guaranteed 401, not a sync attempt. (recv() also resolves with
    // None when task.abort() drops the stub's senders, so "no message"
    // covers both the elapsed window and the closed channel.)
    let got = tokio::time::timeout(Duration::from_millis(50), rx.recv()).await;
    assert!(
        !matches!(got, Ok(Some(_))),
        "a request must not reach the backend when re-addressing fails"
    );
}

#[tokio::test]
async fn app_only_callback_principal_fails_closed_without_user() {
    let (adapter, mut rx, task) = backend_reporting_principal().await;
    let adapter = adapter.with_principal_resigner(resigner());
    let app_only = mint_principal_jwt(
        RESIGNING_KEY,
        "backend",
        json!([{
            "type": "app",
            "tenant": "avernet",
            "app": {
                "app_id": 7,
                "app_name": "work-orders",
                "owners": "152819",
                "tenant": "avernet",
                "app_type": "internal"
            }
        }]),
    );
    let result = adapter.sync(authed_command("abc123:85020", app_only)).await;
    task.abort();
    // The backend endpoint requires a verified *user* principal; an app-only
    // credential could only ever 401, so the port refuses to mint one.
    assert!(result.is_err(), "app-only principal must not be re-addressed");
    let got = tokio::time::timeout(Duration::from_millis(50), rx.recv()).await;
    assert!(
        !matches!(got, Ok(Some(_))),
        "a request must not reach the backend for an app-only principal"
    );
}
