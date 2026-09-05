//! `/auth/user` integration tests.
//!
//! Covers the chain-backed identity resolution for `GET /auth/user`:
//!
//! - **Local / no-OAuth** (`[auth]` with `chain = ["local"]`): `/auth/user` is
//!   mounted (identity-only router) and returns the mock principal from config
//!   or `X-Mock-*` headers. Before the refactor this returned 404 because the
//!   `/auth/*` router required a full OAuth config + provider to mount.
//! - **OAuth cookie**: with a bound `bcs_session` cookie, `/auth/user` returns
//!   the user_id/name/avatar carried from the identity row through
//!   `verify_oauth_session` (proving `AuthPrincipal.avatar` propagation).
//! - **Anonymous**: when no plugin yields a principal, 401 is returned.
//! - **Protocol routes stay absent without OAuth**: `/auth/url` is 404 in the
//!   local-only config (identity-only mounts just `/auth/user`).

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::{json, Value};

use bcs::{BcsConfig, BcsError, BcsServer};

/// Build a minimal in-memory BCS config. `auth` is spliced in verbatim.
fn config_with_auth(bots_dir: &PathBuf, auth: Value) -> BcsConfig {
    let config_json = json!({
        "bind": "127.0.0.1",
        "port": 0,
        "bots_base_dir": bots_dir,
        "max_history_per_session": 100,
        "store_messages": true,
        "max_groups_as_driver": 3,
        "group_chat_delay_min_ms": 0,
        "group_chat_delay_max_ms": 0,
        "max_group_members": 5,
        "max_groups_as_member": 10,
        "max_group_messages": 100,
        "onboard_binding_enabled": false,
        "strict_container_validation": false,
        "bcs_endpoint": null,
        "default_visibility": null,
        "auth": auth,
        "logging": {
            "default_level": "info",
            "console": true,
            "modules": {},
            "tags": {},
            "outputs": []
        }
    });
    serde_json::from_value(config_json).expect("Failed to parse BcsConfig")
}

async fn start(
    config: BcsConfig,
) -> (SocketAddr, tokio::task::JoinHandle<Result<(), BcsError>>) {
    BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port()
        .await
        .expect("Failed to start server")
}

const TEST_JWT_SECRET: &str = "test-secret-key-at-least-32-bytes!!";

// ---------------------------------------------------------------------------
// Local / no-OAuth config
// ---------------------------------------------------------------------------

/// With `chain = ["local"]` and a mock user_id, but NO `[auth.oauth]`, the
/// identity-only router mounts `GET /auth/user` and resolves the local mock
/// principal from config.
#[tokio::test]
async fn auth_user_returns_local_mock_when_no_oauth() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
        json!({
            "chain": ["local"],
            "mock_user_id": "000000",
            "mock_user_name": "guest",
            "allow_mock_headers": true,
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user");
    assert_eq!(resp.status(), 200, "/auth/user must be mounted for local-only config");

    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["user_id"], "000000", "config mock_user_id");
    assert_eq!(body["name"], "guest", "config mock_user_name");
    assert_eq!(body["provider"], "Local", "local source name");
    assert_eq!(body["avatar"], Value::Null, "mock principal has no avatar");

    handle.abort();
}

/// `X-Mock-*` headers override the config defaults when `allow_mock_headers`
/// is enabled.
#[tokio::test]
async fn auth_user_local_headers_override_config() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
        json!({
            "chain": ["local"],
            "mock_user_id": "000000",
            "mock_user_name": "guest",
            "allow_mock_headers": true,
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user"))
        .header("X-Mock-User-Id", "999")
        .header("X-Mock-Nick-Name", "Alice")
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user");
    assert_eq!(resp.status(), 200);

    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["user_id"], "999", "header overrides user_id");
    assert_eq!(body["name"], "Alice", "header overrides name");
    assert_eq!(body["provider"], "Local");

    handle.abort();
}

/// When the local plugin yields no principal (no mock_user_id, no headers,
/// `allow_mock_headers = false`), `/auth/user` returns 401.
#[tokio::test]
async fn auth_user_unauthorized_when_anonymous() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
        json!({
            "chain": ["local"],
            "allow_mock_headers": false,
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user");
    assert_eq!(resp.status(), 401, "anonymous must be unauthorized");

    handle.abort();
}

/// In the local-only config, the OAuth protocol routes (`/auth/url`) must NOT
/// be mounted — only `/auth/user` is.
#[tokio::test]
async fn auth_url_absent_in_local_only_config() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
        json!({
            "chain": ["local"],
            "mock_user_id": "000000",
            "mock_user_name": "guest",
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    assert_eq!(
        resp.status(),
        404,
        "/auth/url must be absent when no OAuth provider is configured"
    );

    handle.abort();
}

// ---------------------------------------------------------------------------
// OAuth cookie config — proved Avatar propagation
// ---------------------------------------------------------------------------

/// With `[auth.oauth]` configured and a bound `bcs_session` cookie,
/// `/auth/user` returns user_id/name/avatar carried from the identity row
/// through `verify_oauth_session`. This exercises the `AuthPrincipal.avatar`
/// propagation end-to-end.
#[tokio::test]
async fn auth_user_returns_oauth_name_and_avatar_from_cookie() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
        json!({
            // OAuth-only chain: we want the cookie path to resolve, not the
            // local mock (which, when `mock_user_id` is set, would short-circuit
            // first and mask the cookie). The mixed-chain precedence behavior is
            // exercised conceptually by the local-only tests above.
            "chain": ["oauth_session"],
            "allow_mock_headers": false,
            "oauth": {
                "jwt_secret": TEST_JWT_SECRET,
                "base_url": "https://bcs.example.com",
                "idle_timeout_minutes": 30,
                "cookie_secure": false,
                "providers": {
                    "google": {
                        "client_id": "test-client-id.apps.googleusercontent.com",
                        "client_secret": "test-client-secret",
                    }
                }
            }
        }),
    );

    // Need the in-process state to seed the identity row + bind the JWT hash.
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");

    let user_port = state
        .user_identity_port
        .clone()
        .expect("user_identity_port wired");

    // Seed an identity row with name + avatar (as the OAuth callback would).
    let user_id = user_port
        .ensure_identity(
            "google",
            "ext-123",
            Some("Alice OAuth"),
            Some("https://example.com/a.png"),
            &state.auth_config.oauth.as_ref().expect("oauth config").env,
        )
        .await
        .expect("ensure_identity");

    // Sign a session JWT and bind its hash (as the callback would).
    let jwt_svc = bcs_jwt::JwtService::new(TEST_JWT_SECRET);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let claims = bcs_jwt::Claims {
        sub: user_id.clone(),
        src: "google".to_string(),
        iat: now,
        exp: now + 1800,
        name: None,
    };
    let jwt = jwt_svc.sign(&claims).expect("sign jwt");
    user_port
        .update_token(&user_id, &bcs_jwt::token_hash(&jwt), claims.exp)
        .await
        .expect("bind token");

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user"))
        .header("cookie", format!("bcs_session={jwt}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user");

    assert_eq!(resp.status(), 200, "bound cookie should authenticate");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["user_id"], user_id, "internal user_id from ensure_identity");
    assert_eq!(body["name"], "Alice OAuth", "name carried from identity row");
    assert_eq!(
        body["avatar"],
        "https://example.com/a.png",
        "avatar carried from identity row via AuthPrincipal.avatar"
    );
    assert_eq!(body["provider"], "google", "oauth source from claims.src");

    handle.abort();
}

/// Sanity: when the local plugin is in the chain but `allow_mock_headers` is
/// off and the cookie is unbound, OAuth still does not authenticate and the
/// local plugin yields no principal → 401 (not the mock fallback, because mock
/// identity is unset).
#[tokio::test]
async fn auth_user_rejects_unbound_cookie_without_mock_identity() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
        json!({
            "chain": ["local", "oauth_session"],
            "allow_mock_headers": false,
            "oauth": {
                "jwt_secret": TEST_JWT_SECRET,
                "base_url": "https://bcs.example.com",
                "cookie_secure": false,
                "providers": {
                    "google": {
                        "client_id": "test-client-id.apps.googleusercontent.com",
                        "client_secret": "test-client-secret",
                    }
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    // A well-signed but never-bound JWT: oauth_session rejects it, local has no
    // mock identity → 401.
    let jwt_svc = bcs_jwt::JwtService::new(TEST_JWT_SECRET);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let claims = bcs_jwt::Claims {
        sub: "nonexistent".to_string(),
        src: "google".to_string(),
        iat: now,
        exp: now + 1800,
        name: None,
    };
    let jwt = jwt_svc.sign(&claims).expect("sign jwt");

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user"))
        .header("cookie", format!("bcs_session={jwt}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user");
    assert_eq!(
        resp.status(),
        401,
        "unbound cookie with no mock identity must not authenticate"
    );

    handle.abort();
}
