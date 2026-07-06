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

/// Build a minimal in-memory BCS config; `oauth` (a JSON object or `null`) is
/// spliced into the `auth` block.
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

async fn start(config: BcsConfig) -> (SocketAddr, tokio::task::JoinHandle<Result<(), BcsError>>) {
    BcsServer::new(config)
        .run_on_random_port()
        .await
        .expect("Failed to start server")
}

// __CONTINUE_HERE__

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
