//! Task 11 legacy `/auth/*` entrypoint end-to-end tests (split from the
//! main file for the 1,000-line gate; shared harness lives in `super`).

use std::time::Duration;

use serde_json::{json, Value};

use bcs::{BcsConfig, BcsServer};
use super::{config_with_auth_origins, seed_bound_oauth_cookie, start};

// ---------------------------------------------------------------------------
// Task 11: legacy /auth/* entrypoint browser-binding end-to-end.
//
// The legacy entry uses the SAME secure service as the V1 entry (legacy flow
// namespace), the same browser-binding challenge cookie protocol, and the
// same Cookie-Origin rule for refresh/logout. The old HTTP envelope shapes
// are preserved except the documented security tightenings.
// ---------------------------------------------------------------------------

/// A network-free `mock-google` provider kind registered through the SAME
/// construction path bootstrap uses (`build_oauth_provider`'s extension
/// factory inventory). Resolves to "Mock User"/"ext-mock" offline.
fn mock_google_provider_factory(
    name: &str,
    _cfg: &bcs_config_api::ProviderSettings,
) -> Option<std::sync::Arc<dyn bcs_auth_api::OAuthProvider>> {
    if name == "mock-google" {
        Some(std::sync::Arc::new(bcs_test_support::MockOAuthProvider::new(
            "mock-google", "ext-mock",
        )))
    } else {
        None
    }
}

inventory::submit! {
    bcs::api_auth_wiring::OAuthProviderFactoryRegistration {
        build: mock_google_provider_factory,
    }
}

const LEGACY_ORIGIN: &str = "https://workbench.example";

fn legacy_config(oauth: Value) -> BcsConfig {
    config_with_auth_origins(
        &tempfile::TempDir::new()
            .expect("temp dir")
            .path()
            .to_path_buf(),
        json!({
            "chain": ["oauth_session"],
            "oauth": oauth,
        }),
        &[LEGACY_ORIGIN],
    )
}

fn legacy_oauth_json() -> Value {
    json!({
        "jwt_secret": "test-secret",
        "base_url": "https://bcs.example.com",
        "providers": {
            "mock-google": {
                "client_id": "mock-client",
                "client_secret": "mock-secret",
            }
        }
    })
}

fn dbg_location_of(resp: &reqwest::Response) -> Option<String> {
    resp.headers()
        .get("location")
        .map(|value| value.to_str().expect("ascii").to_string())
}

fn dbg_cookies_of(resp: &reqwest::Response) -> Vec<String> {
    legacy_cookies(resp)
}

/// Capture set-cookie values in order.
fn legacy_cookies(resp: &reqwest::Response) -> Vec<String> {
    resp.headers()
        .get_all(reqwest::header::SET_COOKIE)
        .iter()
        .map(|v| v.to_str().expect("ascii").to_string())
        .collect()
}

#[tokio::test]
async fn legacy_auth_url_sets_challenge_cookie_and_preserves_envelope() {
    let (addr, handle) = start(legacy_config(legacy_oauth_json())).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    assert_eq!(resp.status(), 200, "/auth/url should be mounted");

    // Old envelope shape preserved: bare `{providers: [{name, url}]}`.
    let body: Value = resp.json().await.expect("json body");
    let providers = body["providers"].as_array().expect("providers array");
    assert_eq!(providers.len(), 1);
    assert_eq!(providers[0]["name"], "mock-google");
    let url = providers[0]["url"].as_str().expect("provider url");
    assert!(url.contains("state="), "state must be carried in the URL");

    // Second request: challenge cookie + no-store.
    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    assert_eq!(
        resp.headers().get(reqwest::header::CACHE_CONTROL),
        Some(&reqwest::header::HeaderValue::from_static("no-store"))
    );
    let cookies = legacy_cookies(&resp);
    assert_eq!(cookies.len(), 1, "exactly the challenge cookie");
    let challenge = &cookies[0];
    assert!(challenge.starts_with("__Host-bcs_oauth_login="));
    assert!(challenge.contains("HttpOnly"));
    assert!(challenge.contains("Secure"));
    assert!(challenge.contains("SameSite=Lax"));
    assert!(challenge.contains("Path=/"));
    assert!(challenge.contains("Max-Age=300"));
    // The nonce must never appear in the JSON bodies.
    let nonce = challenge
        .split(';').next().expect("cookie pair")
        .strip_prefix("__Host-bcs_oauth_login=")
        .expect("nonce")
        .to_string();
    assert!(
        !serde_json::to_string(&body).unwrap().contains(&nonce),
        "nonce must not leak into the response body"
    );

    handle.abort();
}

#[tokio::test]
async fn legacy_callback_without_binding_cookie_is_rejected() {
    let (addr, handle) = start(legacy_config(legacy_oauth_json())).await;

    let body: Value = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url")
        .json()
        .await
        .expect("json body");
    let url = body["providers"][0]["url"].as_str().expect("url");
    let state_param = reqwest::Url::parse(url)
        .expect("parse provider url")
        .query_pairs()
        .find(|(k, _)| k == "state")
        .map(|(_, v)| v.into_owned())
        .expect("state param");

    let resp = reqwest::Client::new()
        .get(format!(
            "http://{addr}/auth/callback/mock-google?code=abc&state={state_param}"
        ))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request legacy callback");
    assert_eq!(resp.status(), 400, "missing binding cookie must be rejected");
    let no_cookie = legacy_cookies(&resp);
    let no_location = resp
        .headers()
        .get("location")
        .map(|value| value.to_str().expect("ascii").to_string());
    assert_eq!(
        resp.text().await.expect("body"),
        "invalid state",
        "old plain-text rejection body preserved"
    );
    assert_eq!(no_cookie.len(), 0, "no cookie may be issued");
    assert_eq!(no_location, None);

    handle.abort();
}

#[tokio::test]
async fn legacy_callback_with_binding_succeeds_and_writes_session() {
    let (addr, handle) = start(legacy_config(legacy_oauth_json())).await;

    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/url"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/url");
    let challenge_cookie = legacy_cookies(&resp)[0].clone();
    let pair = challenge_cookie.split(';').next().expect("pair").to_string();
    let state_param: String = {
        let body: Value = resp.json().await.expect("json body");
        let url = body["providers"][0]["url"].as_str().expect("url");
        reqwest::Url::parse(url)
            .expect("parse")
            .query_pairs()
            .find(|(k, _)| k == "state")
            .map(|(_, v)| v.into_owned())
            .expect("state")
    };

    // Browser completes the callback with its OWN challenge cookie.
    let no_redirect = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .expect("client");
    let resp = no_redirect
        .get(format!(
            "http://{addr}/auth/callback/mock-google?code=abc&state={state_param}"
        ))
        .header("cookie", pair)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request legacy callback");
    let dbg_status = resp.status();
    let dbg_location = dbg_location_of(&resp);
    let cookies = dbg_cookies_of(&resp);
    let dbg_body = resp.text().await.unwrap_or_default();
    eprintln!("DBG callback status={} body={} location={:?} cookies={:?}", dbg_status, dbg_body, dbg_location, cookies);
    assert_eq!(dbg_status, 302, "old redirect envelope preserved");
    assert_eq!(dbg_location.as_deref(), Some("/"));

    assert_eq!(cookies.len(), 2, "session Set-Cookie + challenge clear");
    let session = cookies.iter().find(|c| c.starts_with("bcs_session=")).expect("session");
    assert!(session.contains("HttpOnly"));
    let clear = cookies.iter().find(|c| c.starts_with("__Host-bcs_oauth_login=")).expect("clear");
    assert!(clear.contains("Max-Age=0"));
    let session_jwt = session
        .strip_prefix("bcs_session=")
        .and_then(|s| s.split(';').next())
        .expect("session jwt")
        .to_string();

    // The session was written server-side: the hot-path who-am-i accepts it.
    let resp = reqwest::Client::new()
        .get(format!("http://{addr}/auth/user"))
        .header("cookie", format!("bcs_session={session_jwt}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/user");
    assert_eq!(resp.status(), 200, "engine-installed session must authenticate");
    let body: Value = resp.json().await.expect("json body");
    assert!(
        body["user_id"].as_str().is_some_and(|v| !v.is_empty()),
        "user_id must be a non-empty internal id, got {body}"
    );
    assert_eq!(body["name"], "Mock User");
    assert_eq!(body["provider"], "mock-google");

    handle.abort();
}

#[tokio::test]
async fn legacy_refresh_and_logout_honor_origin() {
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(legacy_config(
        legacy_oauth_json(),
    ))
    .run_on_random_port_with_state()
    .await
    .expect("start server with state");
    let (_user_id, jwt) = seed_bound_oauth_cookie(
        &state,
        "mock-google",
        "ext-mock",
        "Alice OAuth",
        "https://example.com/a.png",
    )
    .await;

    // Missing Origin on cookie-backed refresh → 403.
    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/auth/refresh"))
        .header("cookie", format!("bcs_session={jwt}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("refresh");
    assert_eq!(resp.status(), 403);

    // Valid Origin → old 200 + fresh bcs_session cookie.
    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/auth/refresh"))
        .header("cookie", format!("bcs_session={jwt}"))
        .header("origin", LEGACY_ORIGIN)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("refresh");
    assert_eq!(resp.status(), 200, "old refresh envelope preserved");
    let cookies = legacy_cookies(&resp);
    assert!(cookies.iter().any(|c| c.starts_with("bcs_session=")
        && !c.contains(jwt.as_str())));

    // Logout without cookie stays idempotent-success (old envelope).
    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/auth/logout"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("logout");
    assert_eq!(resp.status(), 200);
    assert!(legacy_cookies(&resp)[0].contains("Max-Age=0"));

    // Logout WITH a cookie but without Origin → 403.
    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/auth/logout"))
        .header("cookie", format!("bcs_session={jwt}"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("logout");
    assert_eq!(resp.status(), 403);

    handle.abort();
}
