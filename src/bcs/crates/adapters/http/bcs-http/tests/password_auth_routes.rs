//! Route-level tests for `POST /auth/register` and `POST /auth/login`.
//!
//! Drives the real axum `session_routes` router with `tower::ServiceExt::oneshot`.
//! The state is wired with the real `PasswordAuthServiceImpl` (argon2 + JWT) and
//! an `OAuthSessionPlugin`-backed `AuthPluginChain`, so the test also proves that
//! the JWT issued by register/login resolves end-to-end through the bearer→
//! `GET /auth/user` read path.
//!
//! `oneshot` consumes the service, but `axum::Router` is `Clone` (a cheap `Arc`
//! clone that shares the underlying `OAuthRouteState`), so each test builds the
//! app ONCE and clones it per request — keeping the identity + credential stores
//! shared across register/login/verify calls.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth::PasswordAuthServiceImpl;
use bcs_auth_api::{
    AuthError, AuthPluginChain, OAuthConfig, UserIdentityInfo, UserIdentityPort,
};
use bcs_auth_oauth::OAuthSessionPlugin;
use bcs_http::oauth::{OAuthRouteState, session_routes};
use bcs_jwt::JwtService;
use bcs_service_api::{PasswordAuthService, UserCredentialRepoPort, UserIdentityRepoPort};
use bcs_user_identity::{MemoryUserCredentialRepo, MemoryUserIdentityRepo};
use serde_json::Value;
use tower::ServiceExt;

const JWT_SECRET: &str = "route-test-secret";
const ENV: &str = "dev";
const IDLE_SECS: u64 = 1800;

/// In-memory `UserIdentityPort` wrapping `MemoryUserIdentityRepo`. Mirrors the
/// test double in `bcs-auth/src/lib.rs` tests: delegates all 5 trait methods to
/// the memory repo, translating the repo's `Result<_, String>` into `AuthError`.
struct InMemoryIdentityPort {
    repo: Arc<MemoryUserIdentityRepo>,
}

impl InMemoryIdentityPort {
    fn new() -> Self {
        Self {
            repo: Arc::new(MemoryUserIdentityRepo::new()),
        }
    }
}

#[async_trait]
impl UserIdentityPort for InMemoryIdentityPort {
    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, AuthError> {
        self.repo
            .ensure_identity(auth_source, external_user_id, external_user_name, avatar, env)
            .await
            .map_err(AuthError::LookupFailed)
    }

    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Result<Option<String>, AuthError> {
        Ok(self.repo.lookup_by_user_id(user_id, auth_source).await)
    }

    async fn get_identity_by_token(
        &self,
        token_hash: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(self.repo.get_by_token(token_hash).await.map(|r| UserIdentityInfo {
            user_id: r.user_id,
            auth_source: r.auth_source,
            user_name: r.user_name,
            external_user_name: r.external_user_name,
            avatar: r.avatar,
        }))
    }

    async fn get_identity_by_user_id(
        &self,
        user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(self
            .repo
            .get_by_user_id_display(user_id)
            .await
            .map(|r| UserIdentityInfo {
                user_id: r.user_id,
                auth_source: r.auth_source,
                user_name: r.user_name,
                external_user_name: r.external_user_name,
                avatar: r.avatar,
            }))
    }

    async fn update_token(
        &self,
        user_id: &str,
        token_hash: &str,
        expire_at: u64,
    ) -> Result<(), AuthError> {
        self.repo
            .update_token(user_id, token_hash, expire_at)
            .await
            .map_err(AuthError::LookupFailed)
    }
}

/// Build the router once. Clone it per request (cheap `Arc` clone) so all
/// requests in a test share the same identity + credential stores.
fn build_app() -> axum::Router {
    let identity_port: Arc<dyn UserIdentityPort> = Arc::new(InMemoryIdentityPort::new());
    let credential_repo: Arc<dyn UserCredentialRepoPort> =
        Arc::new(MemoryUserCredentialRepo::new());

    let svc = Arc::new(PasswordAuthServiceImpl::new(
        Arc::clone(&identity_port),
        credential_repo,
        JWT_SECRET,
        ENV.to_string(),
        IDLE_SECS,
    )) as Arc<dyn PasswordAuthService>;

    let chain = Arc::new(AuthPluginChain::new(vec![Box::new(
        OAuthSessionPlugin::new(JWT_SECRET, Arc::clone(&identity_port)),
    )]));

    let config = OAuthConfig {
        jwt_secret: JWT_SECRET.to_string(),
        idle_timeout_minutes: 30,
        base_url: String::new(),
        cookie_secure: false,
        env: ENV.to_string(),
    };

    let state = Arc::new(OAuthRouteState::new(
        JWT_SECRET,
        identity_port,
        std::collections::HashMap::new(),
        config,
        Some(chain),
        svc,
    ));

    session_routes(state)
}

/// Send a JSON POST request through a clone of the app; return (status, body).
async fn post_json(
    app: &axum::Router,
    uri: &str,
    body: &Value,
) -> (StatusCode, Value, Option<String>) {
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let cookie = resp
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    let body = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, body, cookie)
}

/// Send a GET request with a bearer token through a clone of the app.
async fn get_with_bearer(app: &axum::Router, uri: &str, token: &str) -> (StatusCode, Value) {
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(uri)
                .header("authorization", format!("Bearer {token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    let body = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, body)
}

/// Send a POST request with a bearer token (no body) through a clone of the
/// app; return (status, set-cookie header).
async fn post_with_bearer(app: &axum::Router, uri: &str, token: &str) -> (StatusCode, Option<String>) {
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(uri)
                .header("authorization", format!("Bearer {token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let cookie = resp
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    (status, cookie)
}

/// Register alice via POST /auth/register; return (status, body, cookie).
async fn register_alice(app: &axum::Router) -> (StatusCode, Value, Option<String>) {
    post_json(
        app,
        "/auth/register",
        &serde_json::json!({"username":"alice","password":"password1"}),
    )
    .await
}

#[tokio::test]
async fn register_then_token_valid() {
    let app = build_app();

    let (status, body, cookie) = register_alice(&app).await;
    assert_eq!(status, StatusCode::OK, "register body: {body}");

    // Body carries the raw JWT + username.
    let token = body["token"].as_str().expect("token in body");
    assert!(!token.is_empty(), "token must be non-empty");
    assert_eq!(body["username"], "alice");

    // `set-cookie` header starts with the session cookie name and carries the JWT.
    let cookie = cookie.expect("set-cookie header present");
    assert!(
        cookie.starts_with("bcs_session="),
        "cookie must start with bcs_session=, got: {cookie}"
    );
    assert!(cookie.contains(&format!("bcs_session={token}")));

    // The issued JWT verifies under the same secret and carries src="password".
    let claims = JwtService::new(JWT_SECRET).verify(token).expect("jwt verifies");
    assert_eq!(claims.src, "password");

    // Prove the bearer token flows through GET /auth/user (register→issue→verify
    // end-to-end through the real OAuthSessionPlugin read path). `UserInfoResponse`
    // exposes `name` (the internal display name), not `username`.
    let (user_status, user_body) = get_with_bearer(&app, "/auth/user", token).await;
    assert_eq!(user_status, StatusCode::OK, "GET /auth/user body: {user_body}");
    assert_eq!(user_body["name"].as_str().unwrap_or_default(), "alice");
    assert_eq!(user_body["provider"].as_str().unwrap_or_default(), "password");
}

#[tokio::test]
async fn register_duplicate_returns_409() {
    let app = build_app();

    let (first, _body, _cookie) = register_alice(&app).await;
    assert_eq!(first, StatusCode::OK);

    let (second, body, _cookie) = register_alice(&app).await;
    assert_eq!(second, StatusCode::CONFLICT, "duplicate body: {body}");
}

#[tokio::test]
async fn login_bad_password_returns_401() {
    let app = build_app();

    let (status, _body, _cookie) = register_alice(&app).await;
    assert_eq!(status, StatusCode::OK);

    // Login with the wrong password must 401 (same message as unknown user to
    // avoid enumeration).
    let (login_status, body, _cookie) = post_json(
        &app,
        "/auth/login",
        &serde_json::json!({"username":"alice","password":"wrongpass"}),
    )
    .await;
    assert_eq!(login_status, StatusCode::UNAUTHORIZED, "login body: {body}");
}

#[tokio::test]
async fn logout_with_bearer_revokes_token() {
    let app = build_app();

    // Register alice → 200; capture the bearer token from the JSON body.
    let (status, body, _cookie) = register_alice(&app).await;
    assert_eq!(status, StatusCode::OK, "register body: {body}");
    let token = body["token"].as_str().expect("token in body");

    // The issued bearer token resolves through GET /auth/user before logout.
    let (pre_status, _pre_body) = get_with_bearer(&app, "/auth/user", token).await;
    assert_eq!(pre_status, StatusCode::OK, "token should resolve before logout");

    // POST /auth/logout with the bearer header → 200, and the response
    // `set-cookie` clears the session cookie (Max-Age=0).
    let (logout_status, logout_cookie) = post_with_bearer(&app, "/auth/logout", token).await;
    assert_eq!(logout_status, StatusCode::OK, "logout should return 200");
    let logout_cookie = logout_cookie.expect("set-cookie header present on logout");
    assert!(
        logout_cookie.contains("Max-Age=0"),
        "logout must clear the cookie with Max-Age=0, got: {logout_cookie}"
    );

    // Revocation: the stored token hash was cleared by logout, so the bearer
    // token no longer resolves through the OAuthSessionPlugin read path.
    let (post_status, _post_body) = get_with_bearer(&app, "/auth/user", token).await;
    assert_eq!(
        post_status,
        StatusCode::UNAUTHORIZED,
        "bearer token must not resolve after logout (revocation)"
    );
}
