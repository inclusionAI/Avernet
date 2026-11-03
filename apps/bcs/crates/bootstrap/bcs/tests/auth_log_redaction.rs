//! HTTP log-propagation redaction coverage (V1 API auth plan, Task 11 4a).
//!
//! Drives the REAL legacy (`/auth/*`) and V1 (`/openapi/v1/auth/*`)
//! OAuth routers — real handlers, real shared `AuthApplicationService`,
//! real bootstrap bridges (`ConfiguredOAuthProviderBridge`,
//! `StrictSessionPortBridge` over the real engine, `PendingLoginStoreBridge`
//! over the real memory store) — through callback / refresh / logout
//! successes AND dependency failures, capturing every `tracing` event
//! the request produces, and asserts:
//!
//! - request query strings (`code`, `state`), `Authorization`/`Cookie`
//!   header values, response `Set-Cookie` values, and provider raw-error
//!   text NEVER reach the captured log;
//! - the access log and rejection warnings DO keep the method, the
//!   query-less path, the request id, and the stable error categories
//!   (non-vacuous presence markers);
//! - the shared `server/http_middleware.rs` method+path-only logging
//!   behavior is unchanged (nothing here relaxes it).
//!
//! Only the provider plugin and the failing identity port are fakes; both
//! are exactly the injection seams bootstrap owns.

use std::sync::Arc;

use async_trait::async_trait;
use axum::body::{to_bytes, Body};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_test_support::capture_request_logs;
use serde_json::Value;
use tower::ServiceExt;

use bcs::api_auth_ports::{
    ConfiguredOAuthProviderBridge, PendingLoginStoreBridge, StrictSessionPortBridge,
};
use bcs::identity_session_wiring::RepoAuthSessionIdentityPort;
use bcs::identity_wiring::RepoUserIdentityPort;
use bcs::server::BcsServerState;

use bcs_auth_api::oauth_types::{OAuthError, OAuthToken, ProviderUserInfo};
use bcs_auth_api::session::{AuthSessionIdentityPort, SessionScope, SessionStoreError};
use bcs_auth_api::{OAuthConfig, OAuthProvider, UserIdentityPort};
use bcs_auth_oauth::{MemoryPendingOAuthLoginStore, OAuthSessionEngine};
use bcs_http::oauth::OAuthRouteState;
use bcs_jwt::OAuthSessionJwt;
use bcs_service_api::application::v1::AuthService;
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::MemoryUserIdentityRepo;

const JWT_SECRET: &str = "log-redaction-test-secret-32-bytes!!";
const ENV: &str = "redaction-test";

// --- noops needed by ApiState::new (shared shape with the V1 suite) --------
#[path = "../../../adapters/http/bcs-api-http/tests/oauth_browser_binding/noops.rs"]
mod api_state_noops;

// ---------------------------------------------------------------------------
// Fakes that are exactly bootstrap's injection seams.

/// Provider plugin whose raw error text deliberately carries a sentinel:
/// third-party error bodies can echo request input, so the log line must
/// never survive with them (the provider bridge lowers to categories).
struct SentinelProvider {
    fail_exchange: bool,
    tag: &'static str,
}

#[async_trait]
impl OAuthProvider for SentinelProvider {
    fn name(&self) -> &str {
        "mocked"
    }

    fn auth_url(&self, state: &str, redirect_uri: &str) -> String {
        format!("https://provider.test/authorize?state={state}&redirect_uri={redirect_uri}")
    }

    async fn exchange_code(&self, _code: &str, _redirect_uri: &str) -> Result<OAuthToken, OAuthError> {
        if self.fail_exchange {
            Err(OAuthError::TokenExchangeFailed(format!(
                "provider-side raw body, sentinel provider-raw-error-{}",
                self.tag
            )))
        } else {
            Ok(OAuthToken {
                access_token: format!("access-token-sentinel-{}", self.tag),
                token_type: None,
                expires_in: None,
                refresh_token: None,
                extra: Default::default(),
            })
        }
    }

    async fn get_user_info(&self, _token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError> {
        Ok(ProviderUserInfo {
            id: "external-1".to_string(),
            name: Some("Redacted Log User".to_string()),
            email: None,
            avatar: None,
        })
    }
}

/// A strict identity port that is DOWN: every session operation surfaces
/// `Unavailable` — the dependency-failure injection for refresh/logout.
struct UnavailableSessionIdentity;

#[async_trait]
impl AuthSessionIdentityPort for UnavailableSessionIdentity {
    async fn ensure_identity(
        &self,
        _provider: &str,
        _external_user_id: &str,
        _name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, SessionStoreError> {
        Err(SessionStoreError::Unavailable)
    }
    async fn read_session_revision(&self, _scope: &SessionScope) -> Result<u64, SessionStoreError> {
        Err(SessionStoreError::Unavailable)
    }
    async fn get_session_by_hash(
        &self,
        _scope: &SessionScope,
        _hash: &str,
        _now: u64,
    ) -> Result<Option<bcs_auth_api::session::SessionSnapshot>, SessionStoreError> {
        Err(SessionStoreError::Unavailable)
    }
    async fn install_login_session(
        &self,
        _command: bcs_auth_api::session::InstallSession,
    ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
        Err(SessionStoreError::Unavailable)
    }
    async fn rotate_session(
        &self,
        _command: bcs_auth_api::session::RotateSession,
    ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
        Err(SessionStoreError::Unavailable)
    }
    async fn revoke_session(
        &self,
        _scope: &SessionScope,
        _session_id: &str,
    ) -> Result<bcs_auth_api::session::SessionRevoke, SessionStoreError> {
        Err(SessionStoreError::Unavailable)
    }
}

/// Craft a VALIDLY-SIGNED strict session token (signature + claim
/// integrity hold), so the presented credential reaches the session
/// store consult — where the down identity port fails — instead of
/// failing at the signature step. The value doubles as the never-logged
/// sentinel.
fn signed_session_token() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let jwt = OAuthSessionJwt::new(JWT_SECRET);
    jwt.sign(&bcs_jwt::OAuthSessionClaims {
            sub: "dependency-failure-user".to_string(),
            src: "mocked".to_string(),
            env: ENV.to_string(),
            session_id: "dependency-session-id".to_string(),
            revision: 1,
            iat: now,
            exp: now + 1800,
            name: None,
        })
        .expect("crafted strict token")
}

/// Which backing identity port the assembled entries use.
#[derive(Clone, Copy)]
enum IdentityBackend {
    Real,
    Unavailable,
}

/// The REAL single-store wiring bootstrap uses: identity repo + strict
/// session repo over the SAME `MemoryUserIdentityRepo` (display and
/// session state share one store, exactly like `identity_wiring`).
struct RealEntry {
    repo: Arc<MemoryUserIdentityRepo>,
    user_port: Arc<dyn UserIdentityPort>,
    identities: Arc<dyn AuthSessionIdentityPort>,
}

impl RealEntry {
    fn new() -> Self {
        let repo = Arc::new(MemoryUserIdentityRepo::new());
        let user_port: Arc<dyn UserIdentityPort> = Arc::new(RepoUserIdentityPort::new(repo.clone()));
        let identities: Arc<dyn AuthSessionIdentityPort> =
            Arc::new(RepoAuthSessionIdentityPort::new(repo.clone(), repo.clone()));
        Self {
            repo,
            user_port,
            identities,
        }
    }

    fn idle_secs(&self) -> u64 {
        1800
    }
}

fn oauth_config() -> OAuthConfig {
    OAuthConfig {
        jwt_secret: JWT_SECRET.to_string(),
        idle_timeout_minutes: 30,
        base_url: "https://bcs.example.com".to_string(),
        cookie_secure: true,
        env: ENV.to_string(),
        success_redirect_path: "/".to_string(),
    }
}

/// Assemble BOTH real entries over the shared primitives, mirroring
/// `server_run.rs`: one engine per entry, one pending store, one provider
/// bridge; the flows (`legacy` / `v1`) and callback bases differ.
struct Assembled {
    legacy_router: axum::Router,
    v1_router: axum::Router,
    identity: IdentityEntry,
}

enum IdentityEntry {
    Real(RealEntry),
    Unavailable,
}

impl IdentityEntry {
    fn identities(&self) -> Arc<dyn AuthSessionIdentityPort> {
        match self {
            IdentityEntry::Real(entry) => entry.identities.clone(),
            IdentityEntry::Unavailable => Arc::new(UnavailableSessionIdentity),
        }
    }

    /// Seed a strict session through the SAFE install path (only possible
    /// for the real backend).
    async fn seed_session(&self, name: &str) -> Option<String> {
        match self {
            IdentityEntry::Unavailable => None,
            IdentityEntry::Real(entry) => {
                let user_id = entry
                    .repo
                    .ensure_identity("mocked", "external-1", Some(name), None, ENV)
                    .await
                    .expect("seed identity");
                let engine = OAuthSessionEngine::new(
                    OAuthSessionJwt::new(JWT_SECRET),
                    entry.identities.clone(),
                    ENV.to_string(),
                    entry.idle_secs(),
                );
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_secs();
                Some(
                    engine
                        .install(
                            SessionScope {
                                user_id,
                                provider: "mocked".to_string(),
                                env: ENV.to_string(),
                            },
                            Some(name.to_string()),
                            now,
                        )
                        .await
                        .expect("strict install")
                        .token,
                )
            }
        }
    }

    fn engines(&self) -> [std::sync::Arc<OAuthSessionEngine>; 2] {
        let identities = self.identities();
        [
            std::sync::Arc::new(OAuthSessionEngine::new(
                OAuthSessionJwt::new(JWT_SECRET),
                identities.clone(),
                ENV.to_string(),
                1800,
            )),
            std::sync::Arc::new(OAuthSessionEngine::new(
                OAuthSessionJwt::new(JWT_SECRET),
                identities,
                ENV.to_string(),
                1800,
            )),
        ]
    }
}

fn assemble(backend: IdentityBackend, exchange_works: bool, tag: &'static str) -> Assembled {
    let identity = match backend {
        IdentityBackend::Real => IdentityEntry::Real(RealEntry::new()),
        IdentityBackend::Unavailable => IdentityEntry::Unavailable,
    };

    let provider = SentinelProvider {
        fail_exchange: !exchange_works,
        tag,
    };
    let providers = vec![("mocked".to_string(), Arc::new(provider) as Arc<dyn OAuthProvider>)];

    let pending = Arc::new(MemoryPendingOAuthLoginStore::new());
    let [engine_legacy, engine_v1] = identity.engines();
    let provider_bridge = Arc::new(ConfiguredOAuthProviderBridge::new(providers));
    let identity_port = identity.identities();
    let pending_bridge = Arc::new(PendingLoginStoreBridge::new(pending));

    let legacy_service: Arc<dyn AuthService> = Arc::new(bcs_app_auth::AuthApplicationService::new(
        provider_bridge.clone(),
        Arc::new(StrictSessionPortBridge::new(engine_legacy, identity_port.clone(), ENV.to_string())),
        pending_bridge.clone(),
        "/".to_string(),
        "legacy".to_string(),
    ));
    let v1_service: Arc<dyn AuthService> = Arc::new(bcs_app_auth::AuthApplicationService::new(
        provider_bridge.clone(),
        Arc::new(StrictSessionPortBridge::new(engine_v1, identity_port.clone(), ENV.to_string())),
        pending_bridge.clone(),
        "/".to_string(),
        "v1".to_string(),
    ));

    let legacy_state = Arc::new(OAuthRouteState::new(
        legacy_service,
        match &identity {
            IdentityEntry::Real(entry) => entry.user_port.clone(),
            IdentityEntry::Unavailable => Arc::new(RepoUserIdentityPort::new(Arc::new(
                MemoryUserIdentityRepo::new(),
            ))),
        },
        oauth_config(),
        None,
        vec!["https://work.example".to_string()],
    ));
    let legacy_router = bcs_http::oauth::routes(legacy_state).layer(axum::middleware::from_fn(
        bcs_http::gateway_trace::observe_request,
    ));

    let mut api_state = bcs_api_http::ApiState::new(
        Arc::new(api_state_noops::NoopGroupService),
        Arc::new(api_state_noops::NoopSessionService),
        Arc::new(api_state_noops::NoopMessageService),
        Arc::new(api_state_noops::NoopInvitationService),
        Arc::new(api_state_noops::NoopRegisterService),
        Arc::new(api_state_noops::NoopFriendshipService),
        Arc::new(FailingVerifier),
    )
    .with_auth_service(v1_service, "https://bcs.example.com/openapi/v1/auth".to_string());
    api_state = api_state.with_trusted_browser_origins(
        bcs_api_http::TrustedBrowserOrigins::new(vec!["https://work.example".to_string()])
            .expect("valid origin"),
    );
    // Production mounts the access-log middleware on the merged outer
    // router (server_run); mirror that for the in-process V1 slice.
    let v1_router = bcs_api_http::router(api_state).layer(axum::middleware::from_fn(
        bcs_http::gateway_trace::observe_request,
    ));

    Assembled {
        legacy_router,
        v1_router,
        identity,
    }
}

/// The V1 /user gate: irrelevant for the flow family, but the full V1
/// router requires a verifier.
struct FailingVerifier;

#[async_trait]
impl bcs_api_http::PrincipalVerifier for FailingVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<bcs_api_http::VerifiedRequestIdentity, bcs_api_http::PrincipalVerificationError> {
        Err(bcs_api_http::PrincipalVerificationError::Missing)
    }
}

// ---------------------------------------------------------------------------
// Drive helpers.

async fn drive(
    router: &axum::Router,
    method: &str,
    path_and_query: &str,
    extra_headers: &[(&str, &str)],
    request_id: &str,
) -> (StatusCode, Vec<String>, Vec<Value>) {
    let mut builder = Request::builder().method(method).uri(path_and_query);
    builder = builder.header("x-request-id", request_id);
    for (name, value) in extra_headers {
        builder = builder.header(*name, *value);
    }
    let request = builder.body(Body::empty()).expect("request");
    let (response, logs) = capture_request_logs(request_id, async move {
        router.clone().oneshot(request).await.expect("response")
    })
    .await;
    let status = response.status();
    let cookies = response
        .headers()
        .get_all(axum::http::header::SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok().map(str::to_string))
        .collect();
    (status, cookies, logs)
}

fn assert_no_credentials(logs: &[Value], sentinels: &[&str]) {
    let raw = serde_json::to_string(logs).expect("serialized logs");
    for sentinel in sentinels {
        assert!(
            !raw.contains(sentinel),
            "credential sentinel leaked into the captured logs: {sentinel}"
        );
    }
}

/// Non-vacuous presence: the request-method + query-less path + request id
/// survive in the access log.
fn assert_access_log_keeps_method_path_and_request_id(logs: &[Value], request_id: &str, method: &str) {
    let event = logs
        .iter()
        .find(|event| event["fields"]["message"] == "http.request.started")
        .expect("the access log fires");
    assert_eq!(event["fields"]["request_id"], request_id);
    assert_eq!(event["fields"]["method"], method);
    let route = event["fields"]["route"].as_str().expect("route");
    assert!(
        route.split('?').next().unwrap_or(route) == route,
        "the logged route never carries the query string: {route}"
    );
}

/// Discover the login URLs on one entry (ONE request — re-discovery
/// rotates the batch) and return the browser's challenge cookie pair
/// (name=value) plus the minted state.
async fn session_cookie_from_url(
    router: &axum::Router,
    entry_prefix: &str,
    request_id: &str,
) -> (String, String) {
    let request = Request::builder()
        .method("GET")
        .uri(format!("{entry_prefix}/url"))
        .header("x-request-id", request_id)
        .body(Body::empty())
        .expect("url request");
    let capture_result = capture_request_logs(request_id, async move {
        let response = router
            .clone()
            .oneshot(request)
            .await
            .expect("response");
        let status = response.status();
        let cookies: Vec<String> = response
            .headers()
            .get_all(axum::http::header::SET_COOKIE)
            .iter()
            .filter_map(|value| value.to_str().ok().map(str::to_string))
            .collect();
        let bytes = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("body")
            .to_vec();
        (status, cookies, bytes)
    })
    .await;
    let ((status, headers, bytes), _logs) = capture_result;
    assert_eq!(status, StatusCode::OK, "url discovery succeeds");
    let challenge = headers
        .iter()
        .find(|cookie| cookie.starts_with("__Host-bcs_oauth_login="))
        .expect("challenge cookie on discovery")
        .split(';')
        .next()
        .expect("challenge pair")
        .to_string();
    let body: Value = serde_json::from_slice(&bytes).expect("url envelope");
    let field = if entry_prefix.starts_with("/openapi") {
        &body["data"]["providers"][0]["url"]
    } else {
        &body["providers"][0]["url"]
    };
    let state = url_state_of(field.as_str().expect("provider url")).expect("state in url");
    (challenge, state)
}

fn url_state_of(url: &str) -> Option<String> {
    url.split('?')
        .nth(1)?
        .split('&')
        .find_map(|pair| pair.strip_prefix("state="))
        .map(str::to_string)
}

// ---------------------------------------------------------------------------
// Callback: success + dependency failure, legacy and V1.

#[tokio::test]
async fn legacy_callback_success_and_redaction() {
    let assembled = assemble(IdentityBackend::Real, true, "legacy-ok");
    let code_sentinel = "auth-code-sentinel-legacy-ok";
    let (challenge, state) = session_cookie_from_url(
        &assembled.legacy_router,
        "/auth",
        "req-legacy-url-ok",
    )
    .await;

    let (status, cookies, logs) = drive(
        &assembled.legacy_router,
        "GET",
        &format!("/auth/callback/mocked?code={code_sentinel}&state={state}"),
        &[
            ("cookie", &challenge),
            ("authorization", "Bearer authorization-sentinel-legacy-ok"),
        ],
        "req-legacy-callback-ok",
    )
    .await;
    assert_eq!(status, StatusCode::FOUND, "the initiating browser completes");
    assert_eq!(cookies.len(), 2, "session install + challenge clear: {cookies:?}");
    let session_cookie = cookies
        .iter()
        .find(|cookie| cookie.starts_with("bcs_session="))
        .expect("session Set-Cookie");
    let session_token = session_cookie
        .split(';')
        .next()
        .and_then(|pair| pair.split_once('=').map(|(_, value)| value.to_string()))
        .expect("session cookie value");

    // Sentinels: the query, the cookie/authorization header values, the
    // response Set-Cookie values, and the browser nonce itself.
    let nonce = challenge
        .split('=')
        .nth(1)
        .map(|rest| rest.split(';').next().unwrap_or_default())
        .expect("challenge pair carries the nonce");
    let mut forbidden: Vec<&str> = vec![
        code_sentinel,
        &state,
        "Bearer authorization-sentinel-legacy-ok",
        "access-token-sentinel-legacy-ok",
        nonce,
    ];
    for cookie in &cookies {
        if let Some(pair) = cookie.split(';').next() {
            forbidden.push(pair);
        }
    }
    assert_no_credentials(&logs, &forbidden);
    assert!(
        !session_token.is_empty(),
        "the response still REALLY sets a session credential (redaction must not strip actual response credentials)"
    );
    // Non-vacuous presence markers.
    assert_access_log_keeps_method_path_and_request_id(&logs, "req-legacy-callback-ok", "GET");
    assert!(
        logs.iter()
            .any(|event| event["fields"]["message"] == "oauth login complete"),
        "the application logs the (category-only) success event"
    );

}

#[tokio::test]
async fn legacy_callback_dependency_failure_is_category_only() {
    let assembled = assemble(IdentityBackend::Real, false, "legacy-fail");
    let code_sentinel = "auth-code-sentinel-legacy-fail";
    let (challenge, state) = session_cookie_from_url(
        &assembled.legacy_router,
        "/auth",
        "req-legacy-url-fail",
    )
    .await;

    let (status, cookies, logs) = drive(
        &assembled.legacy_router,
        "GET",
        &format!("/auth/callback/mocked?code={code_sentinel}&state={state}"),
        &[("cookie", &challenge)],
        "req-legacy-callback-fail",
    )
    .await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR, "pre-mapped legacy shape");
    assert_eq!(cookies.len(), 1, "post-consume failure still clears the challenge");
    assert!(cookies[0].starts_with("__Host-bcs_oauth_login=;"));

    assert_no_credentials(
        &logs,
        &[
            code_sentinel,
            &state,
            "provider-raw-error-legacy-fail",
            "access-token-sentinel-legacy-fail",
        ],
    );
    assert_access_log_keeps_method_path_and_request_id(&logs, "req-legacy-callback-fail", "GET");
    assert!(
        logs.iter().any(|event| event["fields"]["category"] == "token_exchange_failed"),
        "the stable exchange-failure category is logged"
    );
}

#[tokio::test]
async fn v1_callback_success_and_redaction() {
    let assembled = assemble(IdentityBackend::Real, true, "v1-ok");
    let code_sentinel = "auth-code-sentinel-v1-ok";
    let (challenge, state) = session_cookie_from_url(
        &assembled.v1_router,
        "/openapi/v1/auth",
        "req-v1-url-ok",
    )
    .await;

    let (status, cookies, logs) = drive(
        &assembled.v1_router,
        "GET",
        &format!("/openapi/v1/auth/callback/mocked?code={code_sentinel}&state={state}"),
        &[
            ("cookie", &challenge),
            ("authorization", "Bearer authorization-sentinel-v1-ok"),
        ],
        "req-v1-callback-ok",
    )
    .await;
    assert_eq!(status, StatusCode::FOUND);
    assert_eq!(cookies.len(), 2, "session set + challenge clear stay separate");
    let mut forbidden: Vec<&str> = vec![
        code_sentinel,
        &state,
        "Bearer authorization-sentinel-v1-ok",
        "access-token-sentinel-v1-ok",
    ];
    for cookie in &cookies {
        if let Some(pair) = cookie.split(';').next() {
            forbidden.push(pair);
        }
    }
    assert_no_credentials(&logs, &forbidden);
    assert_access_log_keeps_method_path_and_request_id(&logs, "req-v1-callback-ok", "GET");
    assert!(
        logs.iter()
            .any(|event| event["fields"]["message"] == "oauth login complete"),
        "V1 success still logs the category-only event"
    );
}

#[tokio::test]
async fn v1_callback_dependency_failure_is_category_only() {
    let assembled = assemble(IdentityBackend::Real, false, "v1-fail");
    let code_sentinel = "auth-code-sentinel-v1-fail";
    let (challenge, state) = session_cookie_from_url(
        &assembled.v1_router,
        "/openapi/v1/auth",
        "req-v1-url-fail",
    )
    .await;

    let (status, cookies, logs) = drive(
        &assembled.v1_router,
        "GET",
        &format!("/openapi/v1/auth/callback/mocked?code={code_sentinel}&state={state}"),
        &[("cookie", &challenge)],
        "req-v1-callback-fail",
    )
    .await;
    assert_eq!(status, StatusCode::BAD_GATEWAY);
    assert_eq!(cookies.len(), 1, "post-consume cleanup still applies");
    assert_no_credentials(
        &logs,
        &[
            code_sentinel,
            &state,
            "provider-raw-error-v1-fail",
            "access-token-sentinel-v1-fail",
        ],
    );
    assert!(
        logs.iter().any(|event| event["fields"]["category"] == "token_exchange_failed"),
        "V1 failure carries the stable category"
    );
    assert_access_log_keeps_method_path_and_request_id(&logs, "req-v1-callback-fail", "GET");
}

// ---------------------------------------------------------------------------
// Refresh: success + dependency failure, legacy and V1.

async fn refresh_matrix(entry_prefix: &str, v1: bool, failure: bool) {
    let label = if failure { "fail" } else { "ok" };
    let scope = if v1 { "v1" } else { "legacy" };
    let assembled = if failure {
        assemble(IdentityBackend::Unavailable, true, "refresh-fail")
    } else {
        assemble(IdentityBackend::Real, true, "refresh-ok")
    };
    let seeded_token = if !failure {
        // A real strict session on the ASSEMBLED entry's own store,
        // installed through the safe path.
        assembled
            .identity
            .seed_session("Refresh User")
            .await
            .expect("seed")
    } else {
        // A validly-signed strict token: it passes signature/claim
        // verification, so the failure comes from the DOWN identity
        // dependency, not from a bad credential.
        signed_session_token()
    };

    let (status, cookies, logs) = drive(
        if v1 {
            &assembled.v1_router
        } else {
            &assembled.legacy_router
        },
        "POST",
        &format!("{entry_prefix}/refresh"),
        &[
            ("cookie", &format!("bcs_session={seeded_token}")),
            ("origin", "https://work.example"),
        ],
        &format!("req-{scope}-refresh-{label}"),
    )
    .await;

    let expected_body_sentinels: Vec<&str> = vec![&seeded_token, "work.example"];
    assert_no_credentials(
        &logs,
        &expected_body_sentinels,
    );
    // `Presented cookie value + request Origin` must never be logged as
    // header material (Origin VALUE may be logged only as a category —
    // assert the raw Cookie header value).
    if failure {
        assert!(
            status == StatusCode::INTERNAL_SERVER_ERROR || status == StatusCode::SERVICE_UNAVAILABLE,
            "dependency outage surfaces as an error: {status}"
        );
        assert!(
            logs.iter()
                .any(|event| event["fields"]["category"] == "auth_dependency_unavailable"),
            "the stable dependency category is logged"
        );
    } else {
        assert_eq!(status, StatusCode::OK, "trusted-origin refresh succeeds: {status}");
        let rotation = cookies
            .iter()
            .find(|cookie| cookie.starts_with("bcs_session="))
            .expect("rotation Set-Cookie");
        let rotated_token = rotation
            .split(';')
            .next()
            .and_then(|pair| pair.split_once('=').map(|(_, value)| value.to_string()))
            .expect("rotated credential");
        assert_ne!(rotated_token, "", "business assertion: the rotation really happened");
        assert_no_credentials(&logs, &[rotated_token.as_str()]);
    }
    assert_access_log_keeps_method_path_and_request_id(
        &logs,
        &format!("req-{scope}-refresh-{label}"),
        "POST",
    );
}

#[tokio::test]
async fn legacy_refresh_success_and_redaction() {
    refresh_matrix("/auth", false, false).await;
}

#[tokio::test]
async fn legacy_refresh_dependency_failure_is_category_only() {
    refresh_matrix("/auth", false, true).await;
}

#[tokio::test]
async fn v1_refresh_success_and_redaction() {
    refresh_matrix("/openapi/v1/auth", true, false).await;
}

#[tokio::test]
async fn v1_refresh_dependency_failure_is_category_only() {
    refresh_matrix("/openapi/v1/auth", true, true).await;
}

// ---------------------------------------------------------------------------
// Logout: success + dependency failure, legacy and V1.

async fn logout_matrix(entry_prefix: &str, v1: bool, failure: bool) {
    let label = if failure { "fail" } else { "ok" };
    let scope = if v1 { "v1" } else { "legacy" };
    let assembled = if failure {
        assemble(IdentityBackend::Unavailable, true, "logout-fail")
    } else {
        assemble(IdentityBackend::Real, true, "logout-ok")
    };
    let presented = if failure {
        signed_session_token()
    } else {
        assembled
            .identity
            .seed_session("Logout User")
            .await
            .expect("seed")
    };

    let (status, cookies, logs) = drive(
        if v1 {
            &assembled.v1_router
        } else {
            &assembled.legacy_router
        },
        "POST",
        &format!("{entry_prefix}/logout"),
        &[
            ("cookie", &format!("bcs_session={presented}")),
            ("origin", "https://work.example"),
        ],
        &format!("req-{scope}-logout-{label}"),
    )
    .await;

    assert_no_credentials(&logs, &[presented.as_str()]);

    if failure {
        assert_ne!(status, StatusCode::OK, "a failed revoke never reports success");
        assert!(
            logs.iter()
                .any(|event| event["fields"]["category"] == "auth_dependency_unavailable"),
            "the revoke failure keeps the stable dependency category"
        );
    } else {
        assert_eq!(status, StatusCode::OK);
        assert!(cookies.iter().any(|cookie| cookie
            .starts_with("bcs_session=;")
            && cookie.contains("Max-Age=0")));
        assert!(
            logs.iter()
                .any(|event| event["fields"]["message"] == "login session revoked"),
            "the revoke-success marker is logged"
        );
    }
    assert_access_log_keeps_method_path_and_request_id(
        &logs,
        &format!("req-{scope}-logout-{label}"),
        "POST",
    );
}

#[tokio::test]
async fn legacy_logout_success_and_redaction() {
    logout_matrix("/auth", false, false).await;
}

#[tokio::test]
async fn legacy_logout_dependency_failure_is_category_only() {
    logout_matrix("/auth", false, true).await;
}

#[tokio::test]
async fn v1_logout_success_and_redaction() {
    logout_matrix("/openapi/v1/auth", true, false).await;
}

#[tokio::test]
async fn v1_logout_dependency_failure_is_category_only() {
    logout_matrix("/openapi/v1/auth", true, true).await;
}

// The bootstrap state type is importable surface for future wiring tests.
#[allow(dead_code)]
fn type_pinning(_state: &BcsServerState) {}
