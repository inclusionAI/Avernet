//! OAuth route mounting integration tests.
//!
//! Verifies that `/auth/url` is mounted (and returns the google provider's
//! login URL) when `[auth.oauth]` with a google provider is configured, and
//! returns 404 when OAuth is not configured.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::{json, Value};

use bcs::{BcsConfig, BcsError, BcsServer};

mod legacy;

pub(crate) const TEST_JWT_SECRET: &str = "test-secret-key-at-least-32-bytes!!";

/// Build a minimal in-memory BCS config; `oauth` (a JSON object or `null`) is
/// spliced into the `auth` block.
pub(crate) fn config_with_oauth(bots_dir: &PathBuf, oauth: Value) -> BcsConfig {
    config_with_oauth_origins(bots_dir, oauth, &[])
}

pub(crate) fn config_with_oauth_origins(bots_dir: &PathBuf, oauth: Value, origins: &[&str]) -> BcsConfig {
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
        "cors": {"allowed_origins": origins},
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

pub(crate) fn config_with_auth(bots_dir: &PathBuf, auth: Value) -> BcsConfig {
    config_with_auth_origins(bots_dir, auth, &[])
}

pub(crate) fn config_with_auth_origins(bots_dir: &PathBuf, auth: Value, origins: &[&str]) -> BcsConfig {
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
        "cors": {"allowed_origins": origins},
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

pub(crate) async fn seed_bound_oauth_cookie(
    state: &std::sync::Arc<bcs::BcsServerState>,
    provider: &str,
    external_user_id: &str,
    name: &str,
    avatar: &str,
) -> (String, String) {
    // Session seeding goes through the SAFE install path (the same
    // OAuthSessionEngine the server wiring uses), never the removed
    // unconditional `update_token` back door.
    let identities = state.auth_session_identities.clone().expect("session identities");
    let user_id = identities
        .ensure_identity(provider, external_user_id, Some(name), Some(avatar), &state_env(state))
        .await
        .expect("ensure_identity");
    let engine = state.oauth_session_engine.clone().expect("oauth engine");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let issued = engine
        .install(
            bcs_auth_api::SessionScope {
                user_id: user_id.clone(),
                provider: provider.to_string(),
                env: state_env(state),
            },
            Some(name.to_string()),
            now,
        )
        .await
        .expect("install login session");
    (user_id, issued.token)
}

pub(crate) fn state_env(state: &std::sync::Arc<bcs::BcsServerState>) -> String {
    state
        .auth_config
        .oauth
        .as_ref()
        .expect("oauth config")
        .env
        .clone()
}

pub(crate) async fn start(config: BcsConfig) -> (SocketAddr, tokio::task::JoinHandle<Result<(), BcsError>>) {
    BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port()
        .await
        .expect("Failed to start server")
}

// __CONTINUE_HERE__

// Seam under test: public HTTP OpenAPI v1 auth facade. These tests assert
// versioned status/envelope/cookie behavior without reaching into provider
// implementation internals.

#[tokio::test]
async fn openapi_bots_mine_rejects_bound_oauth_cookie_without_gateway_principal() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
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
        }),
    );
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (_user_id, jwt) = seed_bound_oauth_cookie(
        &state,
        "google",
        "ext-123",
        "Alice OAuth",
        "https://example.com/a.png",
    )
    .await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/collaboration/bots/mine"))
        .header("cookie", format!("bcs_session={jwt}"))
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
            "jwt_secret": "test-secret",
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
            "jwt_secret": "test-secret",
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
            "jwt_secret": "test-secret",
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
    let set_cookie = resp
        .headers()
        .get(reqwest::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("set-cookie header");
    assert!(set_cookie.contains("bcs_session="));
    assert!(set_cookie.contains("Max-Age=0"));
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
            "jwt_secret": "test-secret",
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
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 40_000);
    assert_eq!(body["data"]["error_code"], "invalid_state");

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_callback_uses_provider_state_binding() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": "test-secret",
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

    let body: Value = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/url")
        .json()
        .await
        .expect("json body");
    let provider_url = body["data"]["providers"][0]["url"].as_str().expect("provider url");
    let parsed = reqwest::Url::parse(provider_url).expect("provider url parses");
    let state = parsed
        .query_pairs()
        .find(|(name, _)| name == "state")
        .map(|(_, value)| value.into_owned())
        .expect("state query param");

    // Task 11 tightening (spec §8.4): the browser-binding challenge cookie is
    // validated BEFORE the state/provider match — a cross-provider replay
    // without the initiating browser's cookie is rejected as invalid_state.
    // (the pending store's atomically bound provider+callback+flow consume
    // covers the replay case for binding-carrying callbacks; asserted at the
    // application-layer contract tests.)
    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/callback/alipay?code=abc&state={state}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/callback/alipay");
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["data"]["error_code"], "invalid_state");

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_user_returns_oauth_identity_from_bound_cookie() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
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
        }),
    );
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (user_id, jwt) = seed_bound_oauth_cookie(
        &state,
        "google",
        "ext-123",
        "Alice OAuth",
        "https://example.com/a.png",
    )
    .await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/user"))
        .header("cookie", format!("bcs_session={jwt}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/user");

    assert_eq!(resp.status(), 200, "bound cookie should authenticate");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["user_id"], user_id);
    assert_eq!(body["data"]["name"], "Alice OAuth");
    assert_eq!(body["data"]["provider"], "google");
    assert_eq!(body["data"]["avatar"], "https://example.com/a.png");

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_refresh_renews_bound_cookie() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(
        &tmp.path().to_path_buf(),
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
        }),
    );
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (_user_id, jwt) = seed_bound_oauth_cookie(
        &state,
        "google",
        "ext-123",
        "Alice OAuth",
        "https://example.com/a.png",
    )
    .await;

    // This configuration has no cors.allowed_origins, so the
    // compat-trusted origin set is empty and a cookie-backed refresh is
    // rejected fail-closed (spec §9 兼容性范围修正).
    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/openapi/v1/auth/refresh"))
        .header("cookie", format!("bcs_session={jwt}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/refresh");

    assert_eq!(resp.status(), 403, "missing Origin on cookie refresh must 403");

    handle.abort();
}

#[tokio::test]
async fn openapi_auth_refresh_honors_trusted_origin_and_renews() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth_origins(
        &tmp.path().to_path_buf(),
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
        }),
        &["https://workbench.example"],
    );
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server with state");
    let (_user_id, jwt) = seed_bound_oauth_cookie(
        &state,
        "google",
        "ext-123",
        "Alice OAuth",
        "https://example.com/a.png",
    )
    .await;

    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/openapi/v1/auth/refresh"))
        .header("cookie", format!("bcs_session={jwt}"))
        .header("origin", "https://workbench.example")
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/refresh");

    assert_eq!(resp.status(), 200, "trusted-Origin refresh must succeed");
    let set_cookie = resp
        .headers()
        .get(reqwest::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("set-cookie header");
    assert!(set_cookie.starts_with("bcs_session="));
    assert!(!set_cookie.contains(&jwt), "refresh should issue a new JWT");
    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 20_000);

    handle.abort();
}

/// OpenAPI Auth facade exposes the same configured providers under a versioned
/// public contract and uses the OpenAPI callback path in redirect_uri.
#[tokio::test]
async fn openapi_auth_url_returns_google_login_url_with_openapi_callback() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": "test-secret",
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

    handle.abort();
}

/// `[api.auth]` chain mode: the login URL callback is
/// `{public_base_url}/callback/{provider}` — the configured base already
/// carries the `/openapi/v1/auth` prefix (contract §4.1). Regression: the
/// chain wiring used to append the prefix a second time, so the OAuth
/// redirect_uri carried a doubled path that no provider callback
/// registration could ever match.
///
/// Uses `BcsServer::new` (NOT the private-outbound tests constructor): the
/// chain's `[api.auth]` secrets must resolve through the REAL env secret
/// provider, while the tests constructor injects an in-memory access that
/// only knows the group-session key. `#[serial]` because this test mutates
/// process env consumed by `EnvSecretAccess` snapshots.
#[tokio::test]
#[serial_test::serial]
async fn chain_auth_url_uses_public_base_url_callback_without_path_doubling() {
    let saved_chain_key = std::env::var("BCS_SECRET_CHAIN_SESSION_SIGNING").ok();
    let saved_chain_secret = std::env::var("BCS_SECRET_CHAIN_GITHUB_SECRET").ok();
    let saved_legacy = std::env::var("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE").ok();
    let saved_group = std::env::var("BCS_SECRET_BCN_GROUP_SESSION_WS_JWT").ok();
    unsafe {
        std::env::set_var("BCS_SECRET_CHAIN_SESSION_SIGNING", "chain-test-session-signing");
        std::env::set_var("BCS_SECRET_CHAIN_GITHUB_SECRET", "chain-test-github-client-secret");
        std::env::set_var("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", "chain-test-legacy-key");
        std::env::set_var("BCS_SECRET_BCN_GROUP_SESSION_WS_JWT", "chain-test-group-session-key");
    }
    let restore = || {
        unsafe {
            for (name, saved) in [
                ("BCS_SECRET_CHAIN_SESSION_SIGNING", &saved_chain_key),
                ("BCS_SECRET_CHAIN_GITHUB_SECRET", &saved_chain_secret),
                ("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", &saved_legacy),
                ("BCS_SECRET_BCN_GROUP_SESSION_WS_JWT", &saved_group),
            ] {
                if let Some(v) = saved {
                    std::env::set_var(name, v);
                } else {
                    std::env::remove_var(name);
                }
            }
        }
    };

    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config_json = json!({
        "bind": "127.0.0.1",
        "port": 0,
        "bots_base_dir": tmp.path(),
        "max_history_per_session": 100,
        "store_messages": true,
        "group_chat_delay_min_ms": 0,
        "group_chat_delay_max_ms": 0,
        "onboard_binding_enabled": false,
        "bcs_endpoint": null,
        "default_visibility": null,
        "secret": {"provider": "env"},
        "api": {"auth": {
            "chain": ["github"],
            "public_base_url": "https://bcs.example.com/openapi/v1/auth",
            "session_signing_key_secret": "CHAIN_SESSION_SIGNING",
            "trusted_browser_origins": ["https://workbench.example.com"],
            "github": {
                "client_id": "test-github-client-id",
                "client_secret_secret": "CHAIN_GITHUB_SECRET",
            }
        }},
        "logging": {
            "default_level": "info",
            "console": true,
            "modules": {},
            "tags": {},
            "outputs": []
        }
    });
    let config: BcsConfig =
        serde_json::from_value(config_json).expect("Failed to parse BcsConfig");
    let (addr, handle) = BcsServer::new(config)
        .run_on_random_port()
        .await
        .expect("start chain-mode server");

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /openapi/v1/auth/url");
    assert_eq!(resp.status(), 200, "chain mode /openapi/v1/auth/url is mounted");

    let body: Value = resp.json().await.expect("json body");
    assert_eq!(body["code"], 20_000, "chain auth returns v1 envelope");
    let url = body["data"]["providers"][0]["url"]
        .as_str()
        .expect("provider url");
    assert!(
        url.starts_with("https://github.com/login/oauth/authorize"),
        "github auth endpoint, got: {url}"
    );
    assert!(
        url.contains(
            "redirect_uri=https%3A%2F%2Fbcs.example.com%2Fopenapi%2Fv1%2Fauth%2Fcallback%2Fgithub"
        ),
        "callback is {{public_base_url}}/callback/github without path doubling, got: {url}"
    );
    assert!(
        !url.contains("%2Fopenapi%2Fv1%2Fauth%2Fopenapi"),
        "redirect_uri must not repeat the auth route prefix, got: {url}"
    );

    handle.abort();
    restore();
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
/// google provider's login URL built from the configured client_id/base_url.
#[tokio::test]
async fn auth_url_returns_google_login_url_when_configured() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_oauth(
        &tmp.path().to_path_buf(),
        json!({
            "jwt_secret": "test-secret",
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
            "jwt_secret": "test-secret",
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
            "jwt_secret": "test-secret",
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
            "jwt_secret": "test-secret",
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
