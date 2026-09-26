//! OAuth browser-binding integration tests for the V1 auth entrypoint
//! (spec §8.4, plan Task 11 RED test 1).
//!
//! Two independent "browsers" (cookie jars) are simulated by manual
//! `Cookie` headers:
//!
//! - Browser A: `GET /openapi/v1/auth/url` receives the temporary login
//!   challenge cookie plus the provider URL list.
//! - Browser B replays A's callback query (valid `state` + `code`) WITHOUT
//!   A's challenge cookie → rejected; the provider exchange is never
//!   called and no session cookie is issued.
//! - Browser A completes the callback with its own cookie → the response
//!   carries BOTH the `bcs_session` Set-Cookie AND the challenge-clear
//!   Set-Cookie as DISTINCT headers (never comma-merged), and the JSON
//!   body never contains the nonce.
//!
//! The test drives the REAL V1 auth router over an `AuthApplicationService`
//! wired to counting fakes for the three OAuth ports — the service itself is
//! never stubbed.

#[path = "support/friendship_routes_fixtures.rs"]
mod fixtures;

use std::sync::{Arc, Mutex};

use fixtures::*;
use tower::ServiceExt;

use async_trait::async_trait;
use bcs_app_auth::{AuthApplicationService, AuthApplicationServiceConfig};
use bcs_api_http::TrustedBrowserOrigins;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort,
    PendingLoginBatch, PendingOAuthLoginPort,
};

const CHALLENGE_COOKIE: &str = "__Host-bcs_oauth_login";
const SESSION_COOKIE: &str = "bcs_session";
const ORIGIN: &str = "http://localhost:3000";
const BASE_URL: &str = "https://bcs.example.com/openapi/v1/auth";

// ---------------------------------------------------------------------------
// Counting fakes for the three OAuth ports (through the REAL service).
// ---------------------------------------------------------------------------

struct FakeProvider {
    exchanges: Mutex<Vec<(String, String)>>,
}

impl FakeProvider {
    fn new() -> Self {
        Self {
            exchanges: Mutex::new(Vec::new()),
        }
    }

    fn exchange_count(&self) -> usize {
        self.exchanges.lock().unwrap().len()
    }
}

#[async_trait]
impl OAuthProviderPort for FakeProvider {
    async fn names(&self) -> Vec<String> {
        vec!["google".to_string()]
    }

    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        _redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        Ok(format!("https://accounts.example/{provider}?state={state}"))
    }

    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        _redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        self.exchanges
            .lock()
            .unwrap()
            .push((provider.to_string(), code.to_string()));
        Ok(ExternalLoginIdentity {
            provider: provider.to_string(),
            external_user_id: "external-1".to_string(),
            name: Some("Alice".to_string()),
            avatar: None,
        })
    }
}

#[derive(Default)]
struct FakeSession {
    installs: Mutex<Vec<String>>,
    refreshes: Mutex<Vec<String>>,
    revocations: Mutex<Vec<String>>,
    next_token: std::sync::atomic::AtomicU64,
}

impl FakeSession {
    fn next(&self) -> u64 {
        self.next_token
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst)
            + 1
    }
}

impl FakeSession {
    fn installed(&self) -> usize {
        self.installs.lock().unwrap().len()
    }
}

#[async_trait]
impl OAuthSessionPort for FakeSession {
    async fn install_identity(
        &self,
        _identity: ExternalLoginIdentity,
        _now: u64,
    ) -> Result<bcs_service_api::port::oauth::BrowserSession, ApplicationError> {
        let token = format!("session-token-{}", self.next());
        self.installs.lock().unwrap().push(token.clone());
        Ok(bcs_service_api::port::oauth::BrowserSession {
            token,
            expires_at: 1_900_000_000,
        })
    }

    async fn refresh(
        &self,
        token: &str,
        _now: u64,
    ) -> Result<bcs_service_api::port::oauth::BrowserSession, ApplicationError> {
        let installed = self.installs.lock().unwrap().contains(&token.to_string());
        if !installed {
            return Err(ApplicationError::Unauthenticated);
        }
        self.refreshes.lock().unwrap().push(token.to_string());
        Ok(bcs_service_api::port::oauth::BrowserSession {
            token: format!("refreshed-{}", token),
            expires_at: 1_900_000_000,
        })
    }

    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        self.revocations.lock().unwrap().push(token.to_string());
        Ok(())
    }
}

#[derive(Default)]
struct FakePending {
    batches: Mutex<Vec<PendingLoginBatch>>,
    consumed: Mutex<Vec<String>>,
}

#[async_trait]
impl PendingOAuthLoginPort for FakePending {
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        _now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError> {
        let index = self.batches.lock().unwrap().len();
        let state = format!("pending-state-{index}");
        let batch = PendingLoginBatch {
            browser_nonce: format!("browser-nonce-{index}"),
            expires_at: 1_900_000_000,
            provider_states: providers
                .iter()
                .map(|p| (p.clone(), state.clone()))
                .collect(),
        };
        assert_eq!(flow, "v1");
        assert!(
            callback_base.ends_with("/callback"),
            "the callback base must be the exact callback prefix, got {callback_base}"
        );
        self.batches.lock().unwrap().push(batch.clone());
        Ok(batch)
    }

    async fn consume(
        &self,
        state: &str,
        browser_nonce: &str,
        provider: &str,
        exact_callback: &str,
        flow: &str,
        _now: u64,
    ) -> Result<(), ApplicationError> {
        // Atomic one-time consumption: an already-consumed state can never
        // match again (the batch is burned on the first full match).
        {
            let consumed = self.consumed.lock().unwrap();
            if consumed.iter().any(|s| s == state) {
                return Err(ApplicationError::invalid(
                    "oauth_invalid_state",
                    "pending batch already consumed",
                ));
            }
        }
        let batches = self.batches.lock().unwrap();
        for pending in batches.iter() {
            let matching = pending.provider_states.iter().any(|(p, s)| {
                s == state
                    && p == provider
                    && pending.browser_nonce == browser_nonce
                    && flow == "v1"
                    && exact_callback
                        == format!("https://bcs.example.com/openapi/v1/auth/callback/{p}")
            });
            if matching {
                self.consumed.lock().unwrap().push(state.to_string());
                return Ok(());
            }
        }
        Err(ApplicationError::invalid(
            "oauth_invalid_state",
            "no matching pending batch",
        ))
    }
}

fn oauth_service() -> (Arc<AuthApplicationService>, Arc<FakeProvider>, Arc<FakeSession>) {
    let provider = Arc::new(FakeProvider::new());
    let session = Arc::new(FakeSession::default());
    let pending = Arc::new(FakePending::default());
    let service = Arc::new(AuthApplicationService::new(
        provider.clone(),
        session.clone(),
        pending,
        AuthApplicationServiceConfig {
            enabled_providers: vec!["google".to_string()],
            flow: "v1".to_string(),
            post_login_redirect: "/app".to_string(),
        },
    ));
    (service, provider, session)
}

fn oauth_state() -> (TestState, Arc<FakeProvider>, Arc<FakeSession>) {
    let (service, provider, session) = oauth_service();
    let state = test_oauth_router(Some(service));
    (state, provider, session)
}

// Router builder shared by both lifecycle scenarios. Defined below the test
// bodies that use it (items are order-independent in Rust).

struct TestState(axum::Router);

fn test_oauth_router(service: Option<Arc<AuthApplicationService>>) -> TestState {
    let api = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(FakeFriendshipService::default()),
        Arc::new(HeaderVerifier {
            caller: caller(),
        }),
    );
    let api = match service {
        Some(service) => api
            .with_auth_service(
                service as Arc<dyn bcs_service_api::application::v1::AuthService>,
                BASE_URL.to_string(),
            )
            .with_trusted_browser_origins(
                TrustedBrowserOrigins::new(vec![ORIGIN.to_string()]).expect("origins"),
            ),
        None => api,
    };
    TestState(router(api))
}

impl TestState {
    async fn call(&self, request: Request<Body>) -> axum::response::Response {
        self.0
            .clone()
            .oneshot(request)
            .await
            .expect("router response")
    }
}

fn get(uri: &str, cookie: Option<&str>, origin: Option<&str>) -> Request<Body> {
    let mut builder = Request::builder()
        .method("GET")
        .uri(uri)
        .header("x-request-id", "request-123");
    if let Some(cookie) = cookie {
        builder = builder.header("cookie", cookie);
    }
    if let Some(origin) = origin {
        builder = builder.header("origin", origin);
    }
    builder.body(Body::empty()).expect("request body")
}

fn post(uri: &str, cookie: Option<&str>, origin: Option<&str>) -> Request<Body> {
    let mut builder = Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-request-id", "request-123");
    if let Some(cookie) = cookie {
        builder = builder.header("cookie", cookie);
    }
    if let Some(origin) = origin {
        builder = builder.header("origin", origin);
    }
    builder.body(Body::from("{}")).expect("request body")
}

/// Collect all `set-cookie` header values in response order.
fn set_cookies(response: &axum::response::Response) -> Vec<String> {
    response
        .headers()
        .get_all(axum::http::header::SET_COOKIE)
        .iter()
        .map(|value| value.to_str().expect("set-cookie ascii").to_string())
        .collect()
}

fn cookie_value<'a>(cookies: &'a [String], name: &str) -> Option<&'a str> {
    cookies.iter().find_map(|c| {
        c.strip_prefix(&format!("{name}="))
            .and_then(|rest| rest.split(';').next())
    })
}

async fn body_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body");
    serde_json::from_slice(&bytes).expect("json body")
}

/// Issue a login URL batch as browser A and return the (nonce, state) pair.
async fn issue_login(
    state: &TestState,
) -> (String, String) {
    let response = state
        .call(get("/openapi/v1/auth/url", Some("other=stale"), None))
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    let cookies = set_cookies(&response);
    let challenge = cookies
        .iter()
        .find(|c| c.starts_with(&format!("{CHALLENGE_COOKIE}=")))
        .expect("challenge cookie set")
        .clone();
    assert!(challenge.contains("HttpOnly"), "challenge must be HttpOnly");
    assert!(challenge.contains("Secure"), "challenge must be Secure (https base)");
    assert!(challenge.contains("SameSite=Lax"));
    assert!(challenge.contains("Path=/"));
    assert!(challenge.contains("Max-Age=300"));
    assert!(
        response
            .headers()
            .get(axum::http::header::CACHE_CONTROL)
            .is_some_and(|v| v == "no-store"),
        "login URL response must be no-store"
    );
    let nonce = cookie_value(&cookies, CHALLENGE_COOKIE).expect("nonce").to_string();

    let body = {
        let bytes = to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let text = std::str::from_utf8(&bytes).unwrap();
        // The nonce must never leak into the JSON body.
        assert!(
            !text.contains(&nonce),
            "nonce must not appear in the login URL response body"
        );
        serde_json::from_str::<Value>(text).expect("providers json")
    };
    let providers = body["data"]["providers"].as_array().expect("providers").clone();
    assert_eq!(providers.len(), 1);
    let url = providers[0]["url"].as_str().expect("url").to_string();
    let parsed = reqwest_like_state(&url);
    (nonce, parsed)
}

/// Minimal query extraction from the fake provider URL (`...?state=<state>`).
fn reqwest_like_state(url: &str) -> String {
    let query = url.split('?').nth(1).expect("query");
    for pair in query.split('&') {
        if let Some(state) = pair.strip_prefix("state=") {
            return state.to_string();
        }
    }
    panic!("no state param in {url}");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[tokio::test]
async fn browser_b_replays_a_callback_without_binding_cookie_and_is_rejected() {
    let (state, provider, session) = oauth_state();
    let (_nonce, url_state) = issue_login(&state).await;

    // Browser B (no cookie jar entry): same query, no challenge cookie.
    let response = state
        .call(get(
            &format!("/openapi/v1/auth/callback/google?code=auth-code&state={url_state}"),
            None,
            None,
        ))
        .await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(
        set_cookies(&response).is_empty(),
        "a rejected cross-browser callback must not issue any cookie"
    );
    let body = body_json(response).await;
    assert_eq!(body["code"], 40_000);
    assert_eq!(body["data"]["error_code"], "invalid_state");

    // The provider was NOT exchanged, and no session was installed.
    assert_eq!(provider.exchange_count(), 0, "provider exchange must not run");
    assert_eq!(session.installed(), 0, "no session may be written");
}

#[tokio::test]
async fn browser_a_completes_login_with_distinct_session_and_challenge_clear_cookies() {
    let (state, provider, session) = oauth_state();
    let (nonce, url_state) = issue_login(&state).await;

    let response = state
        .call(get(
            &format!("/openapi/v1/auth/callback/google?code=auth-code&state={url_state}"),
            Some(&format!("{CHALLENGE_COOKIE}={nonce}")),
            None,
        ))
        .await;
    assert_eq!(response.status(), StatusCode::FOUND);
    assert_eq!(
        response.headers().get("location").expect("location"),
        "/app"
    );
    let cookies = set_cookies(&response);
    assert_eq!(
        cookies.len(),
        2,
        "callback must set the session AND clear the challenge as separate headers, got {cookies:?}"
    );
    let session_cookie = cookies
        .iter()
        .find(|c| c.starts_with(&format!("{SESSION_COOKIE}=")))
        .expect("session cookie");
    let clear_challenge = cookies
        .iter()
        .find(|c| c.starts_with(&format!("{CHALLENGE_COOKIE}=;")))
        .expect("challenge clear cookie");
    assert!(session_cookie.contains("HttpOnly"));
    assert!(session_cookie.contains("Path=/"));
    assert!(clear_challenge.contains("Max-Age=0"));
    assert_ne!(
        set_cookies(&response)[0], set_cookies(&response)[1],
        "the two Set-Cookie instructions must be distinct headers"
    );

    // Login consumed the binding and exchanged exactly once.
    assert_eq!(
        provider.exchange_count(),
        1,
        "exactly one provider exchange follows the matched binding"
    );
    assert_eq!(session.installed(), 1);

    // The match was one-time: replaying A's own state needs the burned batch
    // to fail even WITH the cookie.
    let replay = state
        .call(get(
            &format!("/openapi/v1/auth/callback/google?code=auth-code&state={url_state}"),
            Some(&format!("{CHALLENGE_COOKIE}={nonce}")),
            None,
        ))
        .await;
    assert_eq!(replay.status(), StatusCode::BAD_REQUEST);
    assert_eq!(provider.exchange_count(), 1, "replay must not re-exchange");
    assert_eq!(session.installed(), 1, "replay must not re-install");
}

#[tokio::test]
async fn refresh_requires_origin_and_logout_clears_session() {
    let (state, _provider, session) = oauth_state();
    let (nonce, url_state) = issue_login(&state).await;
    let login = state
        .call(get(
            &format!("/openapi/v1/auth/callback/google?code=auth-code&state={url_state}"),
            Some(&format!("{CHALLENGE_COOKIE}={nonce}")),
            None,
        ))
        .await;
    let cookies = set_cookies(&login);
    let token = cookie_value(&cookies, SESSION_COOKIE).expect("session token").to_string();

    // Missing Origin on a cookie-backed unsafe request → 403.
    let response = state
        .call(post(
            "/openapi/v1/auth/refresh",
            Some(&format!("{SESSION_COOKIE}={token}")),
            None,
        ))
        .await;
    assert_eq!(response.status(), StatusCode::FORBIDDEN, "missing Origin must 403");
    assert_eq!(session.refreshes.lock().unwrap().len(), 0);

    // Mismatched Origin → 403.
    let response = state
        .call(post(
            "/openapi/v1/auth/refresh",
            Some(&format!("{SESSION_COOKIE}={token}")),
            Some("https://attacker.example"),
        ))
        .await;
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Valid Origin → 200 with a fresh session cookie.
    let response = state
        .call(post(
            "/openapi/v1/auth/refresh",
            Some(&format!("{SESSION_COOKIE}={token}")),
            Some(ORIGIN),
        ))
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    let refresh_cookies = set_cookies(&response);
    let rotated = format!("refreshed-{token}");
    assert!(
        refresh_cookies
            .iter()
            .any(|cookie| cookie
                .strip_prefix(&format!("{SESSION_COOKIE}="))
                .and_then(|rest| rest.split(';').next())
                == Some(rotated.as_str())),
        "refresh must set the rotated token, got {refresh_cookies:?}"
    );
    assert_eq!(session.refreshes.lock().unwrap().len(), 1);

    // Logout without Origin → 403 (cookie-backed unsafe request).
    let response = state
        .call(post(
            "/openapi/v1/auth/logout",
            Some(&format!("{SESSION_COOKIE}={token}")),
            None,
        ))
        .await;
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Logout with valid Origin → envelope + clear cookie + server-side revoke.
    let response = state
        .call(post(
            "/openapi/v1/auth/logout",
            Some(&format!("{SESSION_COOKIE}={token}")),
            Some(ORIGIN),
        ))
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(session.revocations.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn auth_user_resolves_human_via_verifier_while_lifecycle_is_auth_not_configured() {
    // No OAuth service mounted on this state.
    let state = test_oauth_router(None);

    for (method, uri) in [
        ("GET", "/openapi/v1/auth/url"),
        ("GET", "/openapi/v1/auth/callback/google?code=c&state=s"),
        ("POST", "/openapi/v1/auth/refresh"),
        ("POST", "/openapi/v1/auth/logout"),
    ] {
        let request = if method == "GET" {
            get(uri, None, None)
        } else {
            post(uri, None, None)
        };
        let response = state.call(request).await;
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{uri}");
        let body = body_json(response).await;
        assert_eq!(body["data"]["error_code"], "auth_not_configured", "{uri}");
    }

    // /user still resolves the Human through the new verifier chain
    // (delivery projection; Gateway caller from the HeaderVerifier fixture).
    let response = state
        .call(authenticated_request("GET", "/openapi/v1/auth/user", Value::Null))
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["user_id"], "staff-1");
}
