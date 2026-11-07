//! V1 OAuth browser-binding HTTP tests (V1 API auth plan, Task 11).
//!
//! Drives the REAL V1 router (`/openapi/v1/auth/*`) over the REAL
//! `AuthApplicationService` with the REAL `MemoryPendingOAuthLoginStore`
//! (only the provider + session ports are in-file fakes), pinning the
//! browser-bound login contract end to end:
//!
//! - URL discovery sets the temporary challenge cookie — `__Host-` name +
//!   the pinned attribute set for `https` public bases, the separate
//!   non-`__Host` name for local HTTP — and the nonce never appears in
//!   any JSON body;
//! - a DIFFERENT browser without the challenge cookie cannot redeem a
//!   valid state, nothing is exchanged or installed, and the initiating
//!   browser's batch stays alive;
//! - the initiating browser's callback answer carries the session install
//!   and the challenge cleanup as TWO SEPARATE `Set-Cookie` headers
//!   (append, never merged) plus `Cache-Control: no-store`;
//! - refresh/logout present the session cookie through the pinned-
//!   provenance Origin gate (`Three-jar` semantics covered by the first
//!   two items; CSRF cases below use their own jars).

#![allow(clippy::expect_used, reason = "test assertions intentionally fail fast")]

use std::sync::Arc;

use async_trait::async_trait;
use axum::body::{to_bytes, Body};
use axum::http::{HeaderMap, Request, StatusCode};
use serde_json::Value;
use tower::ServiceExt;

use bcs_api_http::{ApiState, TrustedBrowserOrigins, router};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedUserQuery, AuthFlowReply, AuthRedirectLocation,
    AuthProviderUrl, AuthProviderUrlList, AuthService, AuthUserInfo, BuildLoginUrls,
    CompleteOAuthLogin, LogoutData, PresentedSession, SessionRenewalData,
};
use bcs_auth_api::login_state::PendingOAuthLoginStore;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort, PendingLoginBatch,
    PendingOAuthLoginPort,
};

// ---------------------------------------------------------------------------
// Fixture services (noops, copied shape-for-shape from manifest_routes.rs).

mod noops {
    include!("oauth_browser_binding/noops.rs");
}

use noops::{NoopFriendshipService, NoopGroupService, NoopInvitationService, NoopMessageService,
            NoopRegisterService, NoopSessionService};

// ---------------------------------------------------------------------------
// Principal verifier stub: the auth family handlers only use it for /user.

struct MissingVerifier;

#[async_trait]
impl bcs_api_http::PrincipalVerifier for MissingVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<bcs_api_http::VerifiedRequestIdentity, bcs_api_http::PrincipalVerificationError>
    {
        Err(bcs_api_http::PrincipalVerificationError::Missing)
    }
}

// ---------------------------------------------------------------------------
// Recording fakes for the provider + session ports.

use std::sync::atomic::{AtomicUsize, Ordering};

struct RecordingProviderPort {
    exchanges: AtomicUsize,
}

impl RecordingProviderPort {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            exchanges: AtomicUsize::new(0),
        })
    }
}

#[async_trait]
impl OAuthProviderPort for RecordingProviderPort {
    fn names(&self) -> Vec<String> {
        vec!["mocked".to_string()]
    }

    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        Ok(format!(
            "https://provider.test/{provider}/authorize?state={state}&redirect_uri={redirect_uri}"
        ))
    }

    async fn exchange_user(
        &self,
        _provider: &str,
        code: &str,
        _redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        self.exchanges.fetch_add(1, Ordering::SeqCst);
        Ok(ExternalLoginIdentity {
            provider: "mocked".to_string(),
            external_user_id: format!("external-{code}"),
            name: Some("Mock User".to_string()),
            avatar: Some("https://img.test/mocked.png".to_string()),
        })
    }
}

struct RecordingSessionPort {
    installs: AtomicUsize,
    rotations: AtomicUsize,
    revocations: AtomicUsize,
}

impl RecordingSessionPort {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            installs: AtomicUsize::new(0),
            rotations: AtomicUsize::new(0),
            revocations: AtomicUsize::new(0),
        })
    }
}

#[async_trait]
impl OAuthSessionPort for RecordingSessionPort {
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        self.installs.fetch_add(1, Ordering::SeqCst);
        Ok(BrowserSession {
            token: format!("session-token-{}-{}", identity.external_user_id, now),
            expires_at: now + 1800,
        })
    }

    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, ApplicationError> {
        self.rotations.fetch_add(1, Ordering::SeqCst);
        if token == "browses-token-current" {
            Ok(BrowserSession {
                token: format!("session-rotated-{now}"),
                expires_at: now + 1800,
            })
        } else {
            Err(ApplicationError::Unauthenticated)
        }
    }

    async fn revoke(&self, _token: &str) -> Result<(), ApplicationError> {
        self.revocations.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

/// The REAL pending-login store behind a thin port adapter: the store's
/// atomic batch consume + browser binding semantics are production code.
struct RealPendingPort {
    store: Arc<bcs_auth_oauth::MemoryPendingOAuthLoginStore>,
}

impl RealPendingPort {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            store: Arc::new(bcs_auth_oauth::MemoryPendingOAuthLoginStore::new()),
        })
    }
}

#[async_trait]
impl PendingOAuthLoginPort for RealPendingPort {
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError> {
        self.store
            .issue_batch(providers, callback_base, flow, now)
            .await
            .map(|batch| PendingLoginBatch {
                browser_nonce: batch.browser_nonce,
                expires_at: batch.expires_at,
                provider_states: batch.provider_states,
            })
            .map_err(|error| match error {
                bcs_auth_api::login_state::LoginStateError::Invalid => {
                    ApplicationError::invalid("invalid_login_state", "login state rejected")
                }
                bcs_auth_api::login_state::LoginStateError::Unavailable => {
                    ApplicationError::unavailable(
                        "auth_dependency_unavailable",
                        "pending login store unavailable",
                    )
                }
            })
    }

    async fn consume(
        &self,
        state: &str,
        browser_nonce: &str,
        provider: &str,
        exact_callback: &str,
        flow: &str,
        now: u64,
    ) -> Result<(), ApplicationError> {
        self.store
            .consume(state, browser_nonce, provider, exact_callback, flow, now)
            .await
            .map_err(|error| match error {
                bcs_auth_api::login_state::LoginStateError::Invalid => {
                    ApplicationError::invalid("invalid_login_state", "login state rejected")
                }
                bcs_auth_api::login_state::LoginStateError::Unavailable => {
                    ApplicationError::unavailable(
                        "auth_dependency_unavailable",
                        "pending login store unavailable",
                    )
                }
            })
    }
}

// ---------------------------------------------------------------------------
// Assembling the real router.

/// The real router plus the recording ports it was wired with, so tests
/// can pin the "nothing was exchanged / installed" side of a rejection.
fn test_router(
    public_base_url: &str,
    trusted_origins: Option<TrustedBrowserOrigins>,
) -> (
    axum::Router,
    Arc<RecordingProviderPort>,
    Arc<RecordingSessionPort>,
) {
    let providers = RecordingProviderPort::new();
    let sessions = RecordingSessionPort::new();
    let pending = RealPendingPort::new();
    let service = Arc::new(bcs_app_auth::AuthApplicationService::new(
        providers.clone(),
        sessions.clone(),
        pending,
        "/after-login".to_string(),
        "v1".to_string(),
    ));
    let mut state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(MissingVerifier),
    )
    .with_auth_service(service, public_base_url.to_string());
    if let Some(origins) = trusted_origins {
        state = state.with_trusted_browser_origins(origins);
    }
    (router(state), providers, sessions)
}

async fn send(app: &axum::Router, request: Request<Body>) -> axum::http::Response<Body> {
    app.clone().oneshot(request).await.expect("router responds")
}

async fn json_body(response: &mut axum::http::Response<Body>) -> Value {
    let bytes = to_bytes(std::mem::take(response.body_mut()), usize::MAX)
        .await
        .expect("response body");
    serde_json::from_slice(&bytes).expect("json envelope")
}

/// The challenge (nonce) value carried by one `Set-Cookie` header.
fn challenge_cookie(response: &axum::http::Response<Body>) -> Option<String> {
    response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .and_then(|cookie| {
            cookie
                .split_once('=')
                .filter(|(name, _)| *name == "__Host-bcs_oauth_login" || *name == "bcs_oauth_login")
                .map(|(_, rest)| rest.split(';').next().unwrap_or_default().to_string())
        })
}

// ---------------------------------------------------------------------------
// Step 1a: URL discovery sets the browser-bound challenge cookie.

#[tokio::test]
async fn url_discovery_sets_the_production_challenge_cookie() {
    let (app, _providers, _sessions) = test_router("https://bcs.example.com/openapi/v1/auth", None);
    let response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/url")
            .body(Body::empty())
            .expect("url request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK, "url discovery succeeds");

    let raw_cookie = response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("the challenge Set-Cookie is present")
        .to_string();
    assert!(
        raw_cookie.starts_with("__Host-bcs_oauth_login="),
        "production base uses the host-only name: {raw_cookie}"
    );
    for attribute in ["Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=300"] {
        assert!(raw_cookie.contains(attribute), "missing {attribute}: {raw_cookie}");
    }
    assert_eq!(
        response
            .headers()
            .get(axum::http::header::CACHE_CONTROL)
            .and_then(|value| value.to_str().ok()),
        Some("no-store")
    );

    // The nonce is cookie-only material: never in the URL list JSON.
    let nonce = challenge_cookie(&response).expect("nonce value");
    assert!(!nonce.is_empty());
    let body = json_body(&mut { response }).await;
    let raw_body = serde_json::to_string(&body).expect("serialized envelope");
    assert!(
        !raw_body.contains(&nonce),
        "the nonce must never appear in the JSON body"
    );
    let providers = body["data"]["providers"].as_array().expect("providers");
    assert_eq!(providers.len(), 1);
    assert_eq!(providers[0]["name"], "mocked");
    assert!(
        providers[0]["url"]
            .as_str()
            .expect("provider url")
            .contains("state="),
        "the provider URL carries the one-time state"
    );
}

#[tokio::test]
async fn local_http_base_uses_the_separate_non_host_challenge_cookie() {
    let (app, _providers, _sessions) = test_router("http://127.0.0.1:21000/openapi/v1/auth", None);
    let response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/url")
            .body(Body::empty())
            .expect("url request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let raw_cookie = response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("set-cookie on local development")
        .to_string();
    assert!(raw_cookie.starts_with("bcs_oauth_login="), "local name: {raw_cookie}");
    assert!(!raw_cookie.contains("__Host-"));
    assert!(!raw_cookie.contains("Secure"));
}

// ---------------------------------------------------------------------------
// Step 1b: two independent cookie jars — browser B (no challenge cookie)
// cannot redeem browser A's valid state, nothing is exchanged/installed,
// and A's batch survives for A to complete.

struct Browser {
    jar: Vec<String>,
}

impl Browser {
    fn new() -> Self {
        Self { jar: Vec::new() }
    }

    fn store_from(&mut self, response: &axum::http::Response<Body>) {
        for value in response.headers().get_all(axum::http::header::SET_COOKIE) {
            if let Some(cookie) = value.to_str().ok() {
                let pair = cookie.split(';').next().unwrap_or("").trim().to_string();
                let name = pair.split('=').next().unwrap_or("").trim().to_string();
                let keep_current_session_only =
                    name.starts_with("bcs_session") || name.contains("oauth_login");
                // A cookie jar keeps the newest instance of a name;
                // challenge/session clears simply drop the name.
                let cleared = pair.split_once('=').map(|(_, value)| value.is_empty());
                if cleared == Some(true) {
                    self.jar.retain(|stored| {
                        !stored.split('=').next().unwrap_or("").trim().eq(&name)
                    });
                } else {
                    self.jar.retain(|stored| {
                        !stored.split('=').next().unwrap_or("").trim().eq(&name)
                            || !keep_current_session_only
                    });
                    self.jar.push(pair);
                }
            }
        }
    }

    fn cookie_header(&self) -> Option<String> {
        if self.jar.is_empty() {
            None
        } else {
            Some(self.jar.join("; "))
        }
    }
}

fn callback_request(path_and_query: &str, cookies: Option<String>) -> Request<Body> {
    let mut builder = Request::builder().method("GET").uri(path_and_query);
    if let Some(cookies) = cookies.as_deref() {
        builder = builder.header("cookie", cookies);
    }
    builder.body(Body::empty()).expect("callback request")
}

#[tokio::test]
async fn cross_browser_callback_fails_without_exchange_and_a_still_completes() {
    let (app, providers, sessions) =
        test_router("https://bcs.example.com/openapi/v1/auth", None);
    let mut browser_a = Browser::new();
    let browser_b = Browser::new();

    // Browser A discovers the login URLs and receives the challenge cookie.
    let mut response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/url")
            .body(Body::empty())
            .expect("url request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    browser_a.store_from(&response);
    let body = json_body(&mut response).await;
    let provider_url = body["data"]["providers"][0]["url"].as_str().expect("url");
    let base = reqwest_url_query(provider_url, "state").expect("state");
    let nonce = browser_a
        .cookie_header()
        .expect("browser A carries the challenge cookie");
    assert!(nonce.contains("oauth_login="));

    // Browser B presents the SAME valid state + code but NO challenge
    // cookie: rejected as invalid_state, with no exchange and no install.
    let b_response = send(
        &app,
        callback_request(
            &format!("/openapi/v1/auth/callback/mocked?code=code-b&state={base}"),
            browser_b.cookie_header(),
        ),
    )
    .await;
    assert_eq!(b_response.status(), StatusCode::BAD_REQUEST, "browser B fails");
    let mut b_response = b_response;
    let b_body = json_body(&mut b_response).await;
    assert_eq!(b_body["data"]["error_code"], "invalid_state");
    assert!(
        b_response
            .headers()
            .get(axum::http::header::SET_COOKIE)
            .is_none(),
        "a binding failure changes no cookies"
    );

    // Browser A (WITH the cookie) completes the same batch: the state was
    // not consumed by B's rejected attempt.
    let a_response = send(
        &app,
        callback_request(
            &format!("/openapi/v1/auth/callback/mocked?code=code-a&state={base}"),
            browser_a.cookie_header(),
        ),
    )
    .await;
    assert_eq!(a_response.status(), StatusCode::FOUND, "browser A completes");
    let cookies: Vec<String> = a_response
        .headers()
        .get_all(axum::http::header::SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok().map(str::to_string))
        .collect();
    assert_eq!(cookies.len(), 2, "session set + challenge clear stay separate: {cookies:?}");
    assert!(
        cookies.iter().any(|cookie| cookie.starts_with("bcs_session=session-token-external-code-a")),
        "the session install is its own header: {cookies:?}"
    );
    assert!(
        cookies
            .iter()
            .any(|cookie| cookie.starts_with("__Host-bcs_oauth_login=;")
                && cookie.contains("Max-Age=0")),
        "the challenge cleanup is its own header: {cookies:?}"
    );
    // Two instructions NEVER merge into one comma-separated Set-Cookie.
    assert!(
        cookies.iter().all(|cookie| !cookie.contains(", ")),
        "comma-merged cookies would lose one instruction: {cookies:?}"
    );
    assert_eq!(
        a_response
            .headers()
            .get(axum::http::header::LOCATION)
            .and_then(|value| value.to_str().ok()),
        Some("/after-login"),
        "the configured same-origin redirect"
    );
    assert_eq!(
        a_response
            .headers()
            .get(axum::http::header::CACHE_CONTROL)
            .and_then(|value| value.to_str().ok()),
        Some("no-store")
    );

    // Nothing was exchanged or installed for B's rejected attempt; A's
    // completion accounts for exactly one of each across the scenario.
    assert_eq!(
        providers.exchanges.load(Ordering::SeqCst),
        1,
        "only the initiating browser ever reached the provider"
    );
    assert_eq!(sessions.installs.load(Ordering::SeqCst), 1);
}

/// Minimal query-param extraction without importing urlencoding helpers.
fn reqwest_url_query(raw_url: &str, name: &str) -> Option<String> {
    raw_url
        .split('?')
        .nth(1)?
        .split('&')
        .find_map(|pair| {
            let (key, value) = pair.split_once('=')?;
            (key == name).then(|| value.to_string())
        })
}

// ---------------------------------------------------------------------------
// Step 1c: the cookie CSRF gate on refresh/logout through the pinned
// provenance (OAuthSessionCookie) and the trusted-origin list.

fn origins(origin: &str) -> TrustedBrowserOrigins {
    TrustedBrowserOrigins::new(vec![origin.to_string()]).expect("valid origin")
}

#[tokio::test]
async fn refresh_requires_an_exactly_trusted_origin_for_cookie_writes() {
    // No trusted origins composed => a valid cookie still fails closed.
    let (app, _providers, _sessions) = test_router("https://bcs.example.com/openapi/v1/auth", None);
    let response = send(
        &app,
        Request::builder()
            .method("POST")
            .uri("/openapi/v1/auth/refresh")
            .header("cookie", "bcs_session=browses-token-current")
            .header("origin", "https://work.example")
            .body(Body::empty())
            .expect("refresh request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::FORBIDDEN, "uncomposed origins fail closed");
    let mut response = response;
    let body = json_body(&mut response).await;
    assert_eq!(body["data"]["error_code"], "browser_origin_untrusted");

    // Composed + matching origin => the rotation lands in a Set-Cookie.
    let (app, _providers, _sessions) = test_router(
        "https://bcs.example.com/openapi/v1/auth",
        Some(origins("https://work.example")),
    );
    let response = send(
        &app,
        Request::builder()
            .method("POST")
            .uri("/openapi/v1/auth/refresh")
            .header("cookie", "bcs_session=browses-token-current")
            .header("origin", "https://work.example")
            .body(Body::empty())
            .expect("refresh request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK, "trusted origin refreshes");
    let cookie = response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("rotation set-cookie");
    assert!(cookie.starts_with("bcs_session=session-rotated-"), "rotated cookie: {cookie}");

    // Composed but a DIFFERENT origin => forbidden, no rotation.
    let (app, _providers, _sessions) = test_router(
        "https://bcs.example.com/openapi/v1/auth",
        Some(origins("https://work.example")),
    );
    let response = send(
        &app,
        Request::builder()
            .method("POST")
            .uri("/openapi/v1/auth/refresh")
            .header("cookie", "bcs_session=browses-token-current")
            .header("origin", "https://evil.example")
            .body(Body::empty())
            .expect("refresh request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .is_none());
}

#[tokio::test]
async fn logout_rejects_an_empty_cookie_and_honors_the_cookie_less_boundary() {
    let (app, _providers, _sessions) = test_router(
        "https://bcs.example.com/openapi/v1/auth",
        Some(origins("https://work.example")),
    );

    // MISSING cookie: valid idempotent boundary — clears the cookie.
    let response = send(
        &app,
        Request::builder()
            .method("POST")
            .uri("/openapi/v1/auth/logout")
            .body(Body::empty())
            .expect("logout request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let cookie = response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("clear set-cookie");
    assert!(cookie.starts_with("bcs_session=;"));
    assert!(cookie.contains("Max-Age=0"));

    // EMPTY cookie value: a malformed credential — never an empty token,
    // never an idempotent success.
    let response = send(
        &app,
        Request::builder()
            .method("POST")
            .uri("/openapi/v1/auth/logout")
            .header("cookie", "bcs_session=")
            .body(Body::empty())
            .expect("logout request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "empty cookie is an error");
    assert!(response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .is_none());
}

// Unused-import guards: the test intentionally pins these names in scope.
#[allow(dead_code)]
fn trait_shape_pins(
    _service: Arc<dyn AuthService>,
    _query: AuthenticatedUserQuery,
    _reply: AuthFlowReply<AuthRedirectLocation>,
    _urls: AuthProviderUrlList,
    _url: AuthProviderUrl,
    _user: AuthUserInfo,
    _build: BuildLoginUrls,
    _login: Option<CompleteOAuthLogin>,
    _presented: PresentedSession,
    _renewal: SessionRenewalData,
    _logout_data: LogoutData,
    _error: ApplicationError,
    _into_response: fn(axum::http::HeaderValue) -> axum::response::Response,
) {
}

// ---------------------------------------------------------------------------
// Compatibility rule (plan §8.2): with NO OAuth configured, V1 `/user`
// still projects the Human that the delivered verifier chain resolved,
// while the whole login family fails closed with `auth_not_configured`.

struct NoopHarnessVerifier;

#[async_trait]
impl bcs_api_http::PrincipalVerifier for NoopHarnessVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<bcs_api_http::VerifiedRequestIdentity, bcs_api_http::PrincipalVerificationError> {
        use bcs_service_api::application::v1::AuthenticatedUserIdentity;
        Ok(bcs_api_http::VerifiedRequestIdentity::pin(
            bcs_service_api::application::v1::AuthenticatedCaller {
                tenant: None,
                user: Some(AuthenticatedUserIdentity {
                    id: "user-no-oauth".to_string(),
                    username: "alice".to_string(),
                    display_name: Some("Alice No-OAuth".to_string()),
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            },
            "chain-projection",
            bcs_api_http::CredentialKind::ExplicitToken,
        ))
    }
}

#[tokio::test]
async fn without_oauth_user_projects_the_verified_human_and_login_fails_closed() {
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(NoopHarnessVerifier),
    );
    let app = router(state);

    let response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/user")
            .body(Body::empty())
            .expect("user request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK, "the Human projection survives");
    let mut response = response;
    let body = json_body(&mut response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["user_id"], "user-no-oauth");
    assert_eq!(body["data"]["provider"], "chain-projection");

    // The login family fails closed without OAuth: every login-family
    // route answers `auth_not_configured`.
    for (method, path) in [
        ("GET", "/openapi/v1/auth/url"),
        ("GET", "/openapi/v1/auth/callback/mocked?code=1&state=2"),
        ("POST", "/openapi/v1/auth/refresh"),
        ("POST", "/openapi/v1/auth/logout"),
    ] {
        let request = Request::builder()
            .method(method)
            .uri(path)
            .body(Body::empty())
            .expect("request");
        let mut response = app.clone().oneshot(request).await.expect("response");
        assert_eq!(
            response.status(),
            StatusCode::NOT_FOUND,
            "{method} {path} must fail closed with auth_not_configured"
        );
        let body = json_body(&mut response).await;
        assert_eq!(body["data"]["error_code"], "auth_not_configured");
    }
}

// ---------------------------------------------------------------------------
// current_user 5xx arms mirror the principal middleware's taxonomy (spec
// §7.4): the SAME PrincipalVerificationError variant must answer with the
// SAME status/error_code the middleware produces — the Bot identity store
// fault keeps its long-standing 502 `bot_identity_unavailable` mapping,
// and only the strict-auth dependency outage is the 503
// `auth_dependency_unavailable` class.

struct FailingHarnessVerifier(bcs_api_http::PrincipalVerificationError);

#[async_trait]
impl bcs_api_http::PrincipalVerifier for FailingHarnessVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<bcs_api_http::VerifiedRequestIdentity, bcs_api_http::PrincipalVerificationError> {
        Err(self.0.clone())
    }
}

fn user_router_with_failing_verifier(
    failure: bcs_api_http::PrincipalVerificationError,
) -> axum::Router {
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(FailingHarnessVerifier(failure)),
    );
    router(state)
}

/// The Bot identity store outage arm: 502 / 50200 / `bot_identity_unavailable`,
/// never folded into the 503 dependency class.
#[tokio::test]
async fn current_user_unavailable_mirrors_the_middleware_502_bot_identity_unavailable() {
    let app = user_router_with_failing_verifier(
        bcs_api_http::PrincipalVerificationError::Unavailable,
    );
    let mut response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/user")
            .body(Body::empty())
            .expect("user request"),
    )
    .await;
    assert_eq!(
        response.status(),
        StatusCode::BAD_GATEWAY,
        "Unavailable keeps the middleware's 502 bot-identity-store mapping"
    );
    let body = json_body(&mut response).await;
    assert_eq!(body["code"], 50_200);
    assert_eq!(body["data"]["error_code"], "bot_identity_unavailable");
}

/// The strict-auth dependency outage arm: 503 / 50300 /
/// `auth_dependency_unavailable`, exactly the middleware's
/// DependencyUnavailable response.
#[tokio::test]
async fn current_user_dependency_unavailable_keeps_the_503_auth_dependency_class() {
    let app = user_router_with_failing_verifier(
        bcs_api_http::PrincipalVerificationError::DependencyUnavailable,
    );
    let mut response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/user")
            .body(Body::empty())
            .expect("user request"),
    )
    .await;
    assert_eq!(
        response.status(),
        StatusCode::SERVICE_UNAVAILABLE,
        "DependencyUnavailable stays the 503 auth-dependency class"
    );
    let body = json_body(&mut response).await;
    assert_eq!(body["code"], 50_300);
    assert_eq!(body["data"]["error_code"], "auth_dependency_unavailable");
}

// ---------------------------------------------------------------------------
// Cookie-name transport confinement (review fix): the callback accepts ONLY
// the mode-correct challenge name — over production HTTPS a local-name
// `Domain`-scoped cookie planted by a hostile subdomain must not satisfy
// the binding, and over local HTTP the `__Host-` name is equally refused.

#[tokio::test]
async fn production_callback_rejects_the_local_http_challenge_name() {
    let (app, providers, sessions) =
        test_router("https://bcs.example.com/openapi/v1/auth", None);
    let mut response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/url")
            .body(Body::empty())
            .expect("url request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let raw_cookie = response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("challenge")
        .to_string();
    let body = json_body(&mut response).await;
    let state = reqwest_url_query(
        body["data"]["providers"][0]["url"].as_str().expect("url"),
        "state",
    )
    .expect("state");
    // The real nonce VALUE, planted under the WRONG (local-HTTP) name: a
    // hostile-subdomain `Domain` cookie carrying the right value must still
    // fail the binding over production HTTPS.
    let real_nonce = raw_cookie
        .split(';')
        .next()
        .and_then(|pair| pair.split_once('=').map(|(_, value)| value))
        .expect("nonce value");

    let b_response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri(format!(
                "/openapi/v1/auth/callback/mocked?code=code-wrong-name&state={state}"
            ))
            .header("cookie", format!("bcs_oauth_login={real_nonce}"))
            .body(Body::empty())
            .expect("callback"),
    )
    .await;
    assert_eq!(
        b_response.status(),
        StatusCode::BAD_REQUEST,
        "the local-HTTP name never satisfies production binding"
    );
    let mut b_response = b_response;
    let b_body = json_body(&mut b_response).await;
    assert_eq!(b_body["data"]["error_code"], "invalid_state");
    // Nothing was exchanged or installed for the wrong-name attempt.
    assert_eq!(providers.exchanges.load(Ordering::SeqCst), 0);
    assert_eq!(sessions.installs.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn local_http_callback_rejects_the_host_challenge_name() {
    let (app, providers, sessions) =
        test_router("http://127.0.0.1:21000/openapi/v1/auth", None);
    let mut response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri("/openapi/v1/auth/url")
            .body(Body::empty())
            .expect("url request"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let raw_cookie = response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|value| value.to_str().ok())
        .expect("challenge")
        .to_string();
    let body = json_body(&mut response).await;
    let state = reqwest_url_query(
        body["data"]["providers"][0]["url"].as_str().expect("url"),
        "state",
    )
    .expect("state");
    let real_nonce = raw_cookie
        .split(';')
        .next()
        .and_then(|pair| pair.split_once('=').map(|(_, value)| value))
        .expect("nonce value");

    let b_response = send(
        &app,
        Request::builder()
            .method("GET")
            .uri(format!(
                "/openapi/v1/auth/callback/mocked?code=code-wrong-name&state={state}"
            ))
            .header("cookie", format!("__Host-bcs_oauth_login={real_nonce}"))
            .body(Body::empty())
            .expect("callback"),
    )
    .await;
    assert_eq!(
        b_response.status(),
        StatusCode::BAD_REQUEST,
        "the __Host- name never satisfies local-HTTP binding"
    );
    let mut b_response = b_response;
    let b_body = json_body(&mut b_response).await;
    assert_eq!(b_body["data"]["error_code"], "invalid_state");
    assert_eq!(providers.exchanges.load(Ordering::SeqCst), 0);
    assert_eq!(sessions.installs.load(Ordering::SeqCst), 0);
}
