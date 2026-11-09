//! OAuth route mounting integration tests (V1 API auth plan, Task 11).
//!
//! Drives the REAL server over the REAL shared application services:
//! both entries (legacy `/auth/*` and V1 `/openapi/v1/auth/*`) now run on
//! the secured browser-bound lifecycle — URL discovery sets the temporary
//! challenge cookie (`__Host-bcs_oauth_login` in production) — and the
//! legacy cookie CSRF gate trusts only the exact `cors.allowed_origins`
//! subset. Sessions are seeded through the SAFE install path (the strict
//! engine over the same in-process identity port), replacing the old
//! unconditional `update_token` backdoor.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::{json, Value};

use bcs::server::BcsServerState;
use bcs::{BcsConfig, BcsError, BcsServer};

const TEST_JWT_SECRET: &str = "test-secret-key-at-least-32-bytes!!";

fn config_with_oauth(bots_dir: &PathBuf, oauth: Value) -> BcsConfig {
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
        "auth": {
            "chain": [],
            "oauth": oauth,
        },
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

fn config_with_auth(bots_dir: &PathBuf, auth: Value) -> BcsConfig {
    config_with_auth_and_cors(bots_dir, auth, Vec::new())
}

fn config_with_auth_and_cors(
    bots_dir: &PathBuf,
    auth: Value,
    allowed_origins: Vec<String>,
) -> BcsConfig {
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
        "cors": { "allowed_origins": allowed_origins },
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

fn full_oauth_config() -> Value {
    json!({
        "chain": ["oauth_session"],
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
    })
}

/// Seed a STRICT session via the safe install path: `ensure_identity` on
/// the same shared store, then the engine's install lifecycle (the login
/// entries use exactly this). Returns `(user_id, session_token)`.
async fn seed_strict_session(
    state: &BcsServerState,
    name: &str,
    avatar: &str,
) -> (String, String) {
    let env = state
        .auth_config
        .oauth
        .as_ref()
        .expect("oauth config")
        .env
        .clone();
    let user_id = state
        .user_identity_port
        .as_ref()
        .expect("user identity port")
        .ensure_identity("google", "ext-123", Some(name), Some(avatar), &env)
        .await
        .expect("ensure_identity");
    let identities = state
        .auth_session_identity
        .clone()
        .expect("strict session identity port wired");
    let engine = bcs_auth_oauth::OAuthSessionEngine::new(
        bcs_jwt::OAuthSessionJwt::new(TEST_JWT_SECRET),
        identities,
        env.clone(),
        1800,
    );
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let session = engine
        .install(
            bcs_auth_api::session::SessionScope {
                user_id: user_id.clone(),
                provider: "google".to_string(),
                env,
            },
            Some(name.to_string()),
            now,
        )
        .await
        .expect("strict session install");
    (user_id, session.token)
}

/// The strict engine over the SAME identity port the real chain verifies
/// against, so the `/auth/user/{user_id}` tests can drive lifecycle
/// mutations (revoke / rotate) on the shared store.
async fn strict_engine_for(state: &BcsServerState) -> bcs_auth_oauth::OAuthSessionEngine {
    let env = state
        .auth_config
        .oauth
        .as_ref()
        .expect("oauth config")
        .env
        .clone();
    let identities = state
        .auth_session_identity
        .clone()
        .expect("strict session identity port wired");
    bcs_auth_oauth::OAuthSessionEngine::new(
        bcs_jwt::OAuthSessionJwt::new(TEST_JWT_SECRET),
        identities,
        env,
        1800,
    )
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

async fn start(config: BcsConfig) -> (SocketAddr, tokio::task::JoinHandle<Result<(), BcsError>>) {
    BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port()
        .await
        .expect("Failed to start server")
}

fn challenge_cookie_from(response: &reqwest::Response) -> Option<String> {
    response
        .headers()
        .get(reqwest::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string)
}

// Seam under test: public HTTP OpenAPI v1 auth facade. These tests assert
// versioned status/envelope/cookie behavior without reaching into provider
// implementation internals.

#[tokio::test]
async fn openapi_bots_mine_rejects_bound_oauth_cookie_without_gateway_principal() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(&tmp.path().to_path_buf(), full_oauth_config());
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (_user_id, token) = seed_strict_session(&state, "Alice OAuth", "https://example.com/a.png").await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/collaboration/bots/mine"))
        .header("cookie", format!("bcs_session={token}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/collaboration/bots/mine");

    assert_eq!(resp.status(), 401, "protected openapi routes require gateway principal");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 40_100);
    assert_eq!(body["data"]["error_code"], "unauthenticated");

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_user_returns_unauthenticated_without_session() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/user"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/user");
    assert_eq!(resp.status(), 401);
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 40_100);
    assert_eq!(body["data"]["error_code"], "unauthenticated");

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_refresh_returns_unauthenticated_without_session() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/openapi/v1/auth/refresh"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/refresh");
    assert_eq!(resp.status(), 401);
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 40_100);
    assert_eq!(body["data"]["error_code"], "unauthenticated");

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_logout_returns_envelope_and_clears_cookie() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/openapi/v1/auth/logout"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/logout");
    assert_eq!(resp.status(), 200);
    let set_cookie = challenge_cookie_from(&resp).expect("set-cookie header");
    assert!(set_cookie.starts_with("bcs_session=;"), "clears the session cookie: {set_cookie}");
    assert!(set_cookie.contains("Max-Age=0"));
    // A logout reply is credential-bearing: never cacheable.
    assert_eq!(
        resp.headers()
            .get(reqwest::header::CACHE_CONTROL)
            .and_then(|value| value.to_str().ok()),
        Some("no-store")
    );
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 20_000);

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_callback_returns_v1_error_for_invalid_state() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/callback/google?code=abc&state=bad-state"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/callback/google");
    assert_eq!(resp.status(), 400);
    assert!(challenge_cookie_from(&resp).is_none(), "no cookies on rejection");
    assert!(resp.headers().get("location").is_none(), "no success redirect");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 40_000);
    assert_eq!(body["data"]["error_code"], "invalid_state");

    handle.abort();
}

/// A state minted for one enabled provider cannot be redeemed through
/// another provider's callback: with the browser challenge cookie present
/// the app rejects the not-enabled provider BEFORE consuming the batch.
#[tokio::test]
async fn openapi_auth_callback_rejects_not_enabled_provider_before_consuming() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let url_resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/url");
    let challenge = challenge_cookie_from(&url_resp).expect("challenge cookie on URL discovery");
    let body: Value = url_resp.json().await.expect("json body");
    let provider_url = body["data"]["providers"][0]["url"].as_str().expect("provider url");
    let state = reqwest::Url::parse(provider_url)
        .expect("provider url parses")
        .query_pairs()
        .find(|(name, _)| name == "state")
        .map(|(_, value)| value.into_owned())
        .expect("state query param");

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/callback/alipay?code=abc&state={state}"))
        .header("cookie", &challenge)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/callback/alipay");
    assert_eq!(resp.status(), 404);
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["data"]["error_code"], "provider_not_enabled");

    handle.abort();
}

/// Without [api.auth], both current-user facades retain the existing
/// strict legacy OAuth chain and its display projection.
#[tokio::test]
async fn compat_current_user_facades_share_the_verified_cookie_identity() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(&tmp.path().to_path_buf(), full_oauth_config());
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (user_id, token) =
        seed_strict_session(&state, "Alice OAuth", "https://example.com/a.png").await;

    // V1 current-user has its own preselected compatibility verifier.
    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/user"))
        .header("cookie", format!("bcs_session={token}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/user");
    assert_eq!(resp.status(), 200, "compat current-user retains the legacy OAuth chain");
    let body: Value = resp.json().await.expect("V1 user JSON");
    assert_eq!(body["data"]["user_id"], user_id);
    assert_eq!(body["data"]["avatar"], "https://example.com/a.png");

    // Legacy /auth/user: the retained identity reader resolves the same
    // strict session through the chain (name/avatar from the trusted row).
    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user"))
        .header("cookie", format!("bcs_session={token}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user");
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["user_id"], user_id);
    assert_eq!(body["name"], "Alice OAuth");
    assert_eq!(body["avatar"], "https://example.com/a.png");
    assert_eq!(body["provider"], "google");

    handle.abort();
}

/// `/auth/user/{user_id}` verifies the presented cookie against the
/// STRICT lifecycle (sid/revision/revocation — spec §6/§8.5), never a
/// raw JWT decode: a valid CURRENT session still serves the exact legacy
/// success shape.
#[tokio::test]
async fn legacy_get_user_serves_a_valid_current_session_with_the_legacy_shape() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(&tmp.path().to_path_buf(), full_oauth_config());
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (user_id, token) =
        seed_strict_session(&state, "Alice OAuth", "https://example.com/a.png").await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user/{user_id}"))
        .header("cookie", format!("bcs_session={token}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user/{user_id}");
    assert_eq!(resp.status(), 200, "a live session serves the self lookup");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["user_id"], user_id);
    assert_eq!(body["name"], "Alice OAuth");
    assert_eq!(body["provider"], "google");
    assert_eq!(body["avatar"], "https://example.com/a.png");

    handle.abort();
}

/// A REVOKED session's cookie must be rejected by the legacy
/// `/auth/user/{user_id}` reader even though its signature and expiry are
/// still perfectly valid (final-review Fix 2: the old handler verified
/// signature+expiry only and served the row).
#[tokio::test]
async fn legacy_get_user_rejects_a_revoked_session_cookie() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(&tmp.path().to_path_buf(), full_oauth_config());
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (user_id, token) =
        seed_strict_session(&state, "Alice OAuth", "https://example.com/a.png").await;
    strict_engine_for(&state)
        .await
        .revoke(&token)
        .await
        .expect("revoke the installed session");

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user/{user_id}"))
        .header("cookie", format!("bcs_session={token}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user/{user_id}");
    assert_eq!(
        resp.status(),
        401,
        "a revoked cookie is not authenticated, signature+expiry notwithstanding"
    );
    let body: Value = resp.json().await.expect("legacy json error shape");
    assert_eq!(body["error"], "not authenticated");

    handle.abort();
}

/// A rotated-out token (refresh moved the instance to a new revision and
/// credential) must be rejected on its user-identity read exactly as on
/// every other strict surface; the ROTATED descendant keeps serving.
#[tokio::test]
async fn legacy_get_user_rejects_a_rotated_out_token_and_serves_the_rotation() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(&tmp.path().to_path_buf(), full_oauth_config());
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (user_id, token) =
        seed_strict_session(&state, "Alice OAuth", "https://example.com/a.png").await;
    let rotated = strict_engine_for(&state)
        .await
        .refresh(&token, unix_now())
        .await
        .expect("rotate the installed session")
        .token;

    let client = reqwest::Client::new();
    let get = |cookie: String| {
        client
            .get(format!("http://{addr}/auth/user/{user_id}"))
            .header("cookie", cookie)
            .timeout(Duration::from_secs(5))
    };
    let stale = get(format!("bcs_session={token}")).send().await.expect("stale token");
    assert_eq!(
        stale.status(),
        401,
        "the rotated-out credential is 'not authenticated' on its read"
    );

    let fresh = get(format!("bcs_session={rotated}"))
        .send()
        .await
        .expect("rotated token");
    assert_eq!(fresh.status(), 200, "the rotation's current credential serves");
    let body: Value = fresh.json().await.expect("json body");
    assert_eq!(body["user_id"], user_id);
    assert_eq!(body["name"], "Alice OAuth");

    handle.abort();
}

#[path = "integration_oauth_routes/compat_origins.rs"]
mod compat_origins;

/// OpenAPI Auth facade exposes the same configured providers under a versioned
/// public contract and uses the OpenAPI callback path in redirect_uri.
#[tokio::test]
async fn openapi_auth_url_returns_google_login_url_with_openapi_callback() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/url");
    assert_eq!(resp.status(), 200, "/openapi/v1/auth/url should be mounted");

    // The V1 public base is https, so the production challenge cookie is
    // the host-only __Host- prefixed one with the pinned attributes and
    // the response is never cacheable.
    let challenge = challenge_cookie_from(&resp).expect("challenge set-cookie");
    assert!(challenge.starts_with("__Host-bcs_oauth_login="), "host-only challenge cookie: {challenge}");
    for attr in ["Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=300"] {
        assert!(challenge.contains(attr), "challenge cookie missing {attr}: {challenge}");
    }
    assert_eq!(
        resp.headers()
            .get(reqwest::header::CACHE_CONTROL)
            .and_then(|value| value.to_str().ok()),
        Some("no-store")
    );

    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 20_000, "OpenAPI auth returns v1 envelope");
    let providers = body["data"]["providers"].as_array().expect("providers array");
    assert_eq!(providers.len(), 1, "exactly one provider configured");
    assert_eq!(providers[0]["name"], "google");
    let url = providers[0]["url"].as_str().expect("provider url");
    assert!(
        url.starts_with("https://accounts.google.com/o/oauth2/v2/auth"),
        "google auth endpoint, got: {url}"
    );
    assert!(
        url.contains("client_id=test-client-id.apps.googleusercontent.com"),
        "carries configured client_id, got: {url}"
    );
    assert!(
        url.contains("redirect_uri=https%3A%2F%2Fbcs.example.com%2Fopenapi%2Fv1%2Fauth%2Fcallback%2Fgoogle"),
        "redirect_uri uses OpenAPI callback path, got: {url}"
    );
    // The nonce is cookie-only material (plan §8.4): never in the JSON.
    match challenge.split(';').next() {
        Some(value) => {
            let nonce = value.split('=').nth(1).unwrap_or_default();
            let raw = serde_json::to_string(&body).expect("serialized body");
            assert!(nonce.is_empty() || !raw.contains(nonce), "nonce must never appear in the JSON body");
        }
        None => panic!("malformed challenge cookie: {challenge}"),
    }

    // A browser WITHOUT the challenge cookie cannot redeem the state.
    let state = reqwest::Url::parse(providers[0]["url"].as_str().expect("provider url"))
        .expect("provider url parses")
        .query_pairs()
        .find(|(name, _)| name == "state")
        .map(|(_, value)| value.into_owned())
        .expect("state query param");
    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/callback/google?code=abc&state={state}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("callback without the challenge cookie");
    assert_eq!(resp.status(), 400, "cross-browser callback must fail");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["data"]["error_code"], "invalid_state");

    handle.abort();
}

/// When OAuth is not configured, the OpenAPI Auth facade fails closed with a
/// versioned error instead of returning a misleading empty provider list.
#[tokio::test]
async fn openapi_auth_url_returns_not_found_when_oauth_absent() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(&tmp.path().to_path_buf(), Value::Null);
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/url");
    assert_eq!(resp.status(), 404, "OpenAPI auth should fail closed");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 40_400);
    assert_eq!(body["data"]["error_code"], "auth_not_configured");

    handle.abort();
}

/// When `[auth.oauth.google]` is configured, `GET /auth/url` returns the
/// google provider's login URL built from the configured client_id/base_url,
/// plus the browser-bound challenge cookie required by the callback.
#[tokio::test]
async fn auth_url_returns_google_login_url_when_configured() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-client-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    assert_eq!(resp.status(), 200, "/auth/url should be mounted");

    let challenge = challenge_cookie_from(&resp).expect("legacy entry also issues the challenge");
    assert!(challenge.starts_with("__Host-bcs_oauth_login="), "https base_url: {challenge}");
    assert!(challenge.contains("Max-Age=300"));

    let body: Value = resp.json().await.expect("json body");
    let providers = body["providers"].as_array().expect("providers array");
    assert_eq!(providers.len(), 1, "exactly one provider configured");
    assert_eq!(providers[0]["name"], "google");
    let url = providers[0]["url"].as_str().expect("provider url");
    assert!(
        url.starts_with("https://accounts.google.com/o/oauth2/v2/auth"),
        "google auth endpoint, got: {url}"
    );
    assert!(
        url.contains("client_id=test-client-id.apps.googleusercontent.com"),
        "carries configured client_id, got: {url}"
    );
    assert!(
        url.contains("redirect_uri=https%3A%2F%2Fbcs.example.com%2Fauth%2Fcallback%2Fgoogle"),
        "redirect_uri built from base_url, got: {url}"
    );

    handle.abort();
}

/// A plain-HTTP (local development) base URL must use the separate
/// non-`__Host` challenge cookie name — production never downgrades.
#[tokio::test]
async fn local_http_base_url_uses_the_non_host_challenge_name() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "http://127.0.0.1:21000",
            "cookie_secure": false,
            "providers": {
                "github": {
                    "client_id": "gh-client-id",
                    "client_secret": "gh-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    assert_eq!(resp.status(), 200);
    let challenge = challenge_cookie_from(&resp).expect("challenge set-cookie");
    assert!(
        challenge.starts_with("bcs_oauth_login="),
        "local HTTP uses the separate name: {challenge}"
    );
    assert!(!challenge.contains("__Host-"));
    assert!(!challenge.contains("Secure"));

    handle.abort();
}

/// When OAuth is not configured, `/auth/url` is not mounted → 404.
#[tokio::test]
async fn auth_url_not_mounted_when_oauth_absent() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(&tmp.path().to_path_buf(), Value::Null);
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
        "/auth/url must be absent when OAuth is not configured"
    );

    handle.abort();
}

/// An empty `jwt_secret` is a misconfiguration: OAuth routes must NOT mount,
/// so an attacker cannot get sessions signed with a guessable/empty key.
#[tokio::test]
async fn auth_url_not_mounted_when_jwt_secret_empty() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": "",
            "base_url": "https://bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "id.apps.googleusercontent.com",
                    "client_secret": "secret",
                }
            }
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
        "empty jwt_secret must keep /auth/* unmounted"
    );

    handle.abort();
}

/// A `base_url` that is not an http(s) URL cannot build valid redirect URIs;
/// OAuth routes must NOT mount rather than emit broken redirects.
#[tokio::test]
async fn auth_url_not_mounted_when_base_url_invalid() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "bcs.example.com",
            "providers": {
                "google": {
                    "client_id": "id.apps.googleusercontent.com",
                    "client_secret": "secret",
                }
            }
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
        "non-http(s) base_url must keep /auth/* unmounted"
    );

    handle.abort();
}

/// Provider `kind` defaults to the instance (map) name, so the common 1:1 case
/// needs no explicit `kind`. A `github`-named instance with no `kind` builds the
/// GitHub provider.
#[tokio::test]
async fn provider_kind_defaults_to_instance_name() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "github": {
                    "client_id": "gh-client-id",
                    "client_secret": "gh-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    assert_eq!(resp.status(), 200, "/auth/url should be mounted");

    let body: Value = resp.json().await.expect("json body");
    let providers = body["providers"].as_array().expect("providers array");
    assert_eq!(providers.len(), 1);
    assert_eq!(providers[0]["name"], "github");
    let url = providers[0]["url"].as_str().expect("provider url");
    assert!(
        url.starts_with("https://github.com/login/oauth/authorize"),
        "github auth endpoint, got: {url}"
    );

    handle.abort();
}

/// Multiple instances of the same `kind` register under distinct names — the
/// capability the old named-field schema could not express.
#[tokio::test]
async fn multiple_instances_of_same_kind() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": TEST_JWT_SECRET,
            "base_url": "https://bcs.example.com",
            "providers": {
                "github-internal": {
                    "kind": "github",
                    "client_id": "internal-id",
                    "client_secret": "internal-secret",
                },
                "github-partner": {
                    "kind": "github",
                    "client_id": "partner-id",
                    "client_secret": "partner-secret",
                }
            }
        }),
    );
    let (addr, handle) = start(config).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    assert_eq!(resp.status(), 200);

    let body: Value = resp.json().await.expect("json body");
    let providers = body["providers"].as_array().expect("providers array");
    assert_eq!(providers.len(), 2, "both instances registered");
    // /auth/url sorts by name.
    assert_eq!(providers[0]["name"], "github-internal");
    assert_eq!(providers[1]["name"], "github-partner");
    // Each carries its own client_id.
    assert!(providers[0]["url"].as_str().unwrap().contains("internal-id"));
    assert!(providers[1]["url"].as_str().unwrap().contains("partner-id"));

    handle.abort();
}
