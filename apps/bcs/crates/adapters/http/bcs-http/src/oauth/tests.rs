//! OAuth route unit tests (Task 11 shape): the legacy handlers are thin,
//! so these pin the ADAPTER contract exactly — challenge-cookie extraction,
//! session-cookie presentation rules, the injected cors-derived Origin
//! gate, cookie-instruction encoding, and the `/auth/user` chain reader.
//! The flow business semantics (binding, atomic consume, install/rotate/
//! revoke) are owned by the shared application service and covered at
//! the bootstrap integration + application levels.
use super::*;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use bcs_test_support::{NoopAuthPlugin, NoopUserIdentityPort};
use super::routes::{
    cookie_unsafe_origin_allowed, encode_cookie_change, extract_login_challenge,
    presented_session_cookie,
};

use async_trait::async_trait;
use axum::body::to_bytes;
use bcs_auth_api::{
    AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, AuthSource, UserIdentityInfo,
    UserIdentityPort,
};
use bcs_service_api::application::v1::{
    AuthFlowReply, AuthRedirectLocation, AuthService, AuthProviderUrl, AuthProviderUrlList,
    AuthenticatedUserQuery, AuthUserInfo, BrowserCookieChange, BuildLoginUrls, CompleteOAuthLogin,
    LogoutData, PresentedSession, SessionRenewalData,
};

/// A chain plugin that always yields a principal with the given `user_id`
/// (which may be `None`), so the empty/missing-user_id branch of
/// `current_user_handler` is reachable without depending on any specific
/// config-driven plugin's input validation.
struct FixedUserPlugin {
    user_id: Option<String>,
}

#[async_trait]
impl AuthPlugin for FixedUserPlugin {
    fn can_authenticate(&self, _headers: &HeaderMap) -> bool {
        true
    }
    async fn authenticate(
        &self,
        _headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        let mut p = AuthPrincipal::new(AuthSource::Local);
        p.user_id = self.user_id.clone();
        Ok(Some(p))
    }
    fn priority(&self) -> u8 {
        10
    }
    fn name(&self) -> &'static str {
        "fixed"
    }
}

fn chain_with(user_id: Option<String>) -> Arc<AuthPluginChain> {
    Arc::new(AuthPluginChain::new(vec![
        Box::new(FixedUserPlugin { user_id }),
        Box::new(NoopAuthPlugin),
    ]))
}

async fn run_current_user(chain: Arc<AuthPluginChain>) -> (StatusCode, serde_json::Value) {
    let state = Arc::new(OAuthRouteState::new_chain_only(
        Arc::new(NoopUserIdentityPort),
        chain,
    ));
    let resp = current_user_handler(State(state), HeaderMap::new())
        .await
        .into_response();
    let status = resp.status();
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.expect("body");
    let body = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
    (status, body)
}

/// A principal whose `user_id` is `None` is not a human login — `/auth/user`
/// must NOT return 200 with an empty user_id.
#[tokio::test]
async fn current_user_rejects_principal_without_user_id() {
    let (status, body) = run_current_user(chain_with(None)).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "no user_id => 401, got {body}");
}

/// A principal whose `user_id` is an empty string must likewise be treated
/// as not authenticated, not as a logged-in user with id "".
#[tokio::test]
async fn current_user_rejects_principal_with_empty_user_id() {
    let (status, body) = run_current_user(chain_with(Some(String::new()))).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "empty user_id => 401, got {body}");
}

/// Regression guard: a populated user_id still returns 200.
#[tokio::test]
async fn current_user_accepts_principal_with_user_id() {
    let (status, body) = run_current_user(chain_with(Some("u-123".to_string()))).await;
    assert_eq!(status, StatusCode::OK, "real user_id => 200, got {body}");
    assert_eq!(body["user_id"], "u-123");
}

// ---------------------------------------------------------------------------
// `/auth/user/{user_id}` verifies through the SAME delegated chain as
// `/auth/user` (no raw JWT decode): a chain-visible principal feeds the
// sub==path self-check, and a chain denial (the strict engine maps a
// revoked/rotated-out/expired cookie to `Ok(None)` at its one legacy
// compatibility exit) is "not authenticated", not a lookup.

/// A user-identity port serving one fixed row, so the success shape is
/// reachable without a store.
struct FixedIdentityPort;

#[async_trait]
impl UserIdentityPort for FixedIdentityPort {
    async fn ensure_identity(
        &self,
        _: &str,
        _: &str,
        _: Option<&str>,
        _: Option<&str>,
        _: &str,
    ) -> Result<String, AuthError> {
        Err(AuthError::LookupFailed("unused in this fake".into()))
    }
    async fn lookup_by_user_id(&self, _: &str, _: &str) -> Result<Option<String>, AuthError> {
        Ok(None)
    }
    async fn get_identity_by_token(&self, _: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }
    async fn get_identity_by_user_id(
        &self,
        user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        if user_id != "u-123" {
            return Ok(None);
        }
        Ok(Some(UserIdentityInfo {
            user_id: "u-123".to_string(),
            auth_source: "google".to_string(),
            user_name: Some("Internal Name".to_string()),
            external_user_name: Some("External Name".to_string()),
            avatar: Some("https://example.com/a.png".to_string()),
        }))
    }
}

async fn run_get_user(
    chain_user_id: Option<String>,
    path_user_id: &str,
) -> (StatusCode, serde_json::Value) {
    let state = Arc::new(OAuthRouteState::new_chain_only(
        Arc::new(FixedIdentityPort),
        chain_with(chain_user_id),
    ));
    let resp = get_user_handler(
        State(state),
        axum::extract::Path(path_user_id.to_string()),
        HeaderMap::new(),
    )
    .await
    .into_response();
    let status = resp.status();
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.expect("body");
    let body = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
    (status, body)
}

/// A chain denial — no principal (a revoked/rotated-out/expired cookie
/// after the legacy `Ok(None)` compatibility exit) — is 401 with the
/// legacy JSON shape, never a lookup.
#[tokio::test]
async fn get_user_rejects_a_denied_session_with_the_legacy_shape() {
    let (status, body) = run_get_user(None, "u-123").await;
    assert_eq!(
        status,
        StatusCode::UNAUTHORIZED,
        "denied credential => 401, got {body}"
    );
    assert_eq!(body["error"], "not authenticated");
    // The identity port is never reached on a denial.
}

/// The sub==path self-check stays: a strictly verified principal for a
/// DIFFERENT subject cannot read that subject's row.
#[tokio::test]
async fn get_user_forbids_a_foreign_subject() {
    let (status, body) = run_get_user(Some("someone-else".to_string()), "u-123").await;
    assert_eq!(status, StatusCode::FORBIDDEN, "foreign sub => 403, got {body}");
    assert_eq!(body["error"], "forbidden");
}

/// Valid current session + matching path: the exact legacy success shape.
#[tokio::test]
async fn get_user_returns_the_legacy_success_shape_for_a_matching_subject() {
    let (status, body) = run_get_user(Some("u-123".to_string()), "u-123").await;
    assert_eq!(status, StatusCode::OK, "self row => 200, got {body}");
    assert_eq!(body["user_id"], "u-123");
    assert_eq!(
        body["name"], "Internal Name",
        "internal display name preferred over the external one"
    );
    assert_eq!(body["provider"], "google");
    assert_eq!(body["avatar"], "https://example.com/a.png");
}

/// A verified self lookup whose identity row is gone keeps the legacy
/// 404 shape (self lookup, but no such identity row anymore).
#[tokio::test]
async fn get_user_returns_the_legacy_not_found_shape_without_a_row() {
    let (status, body) = run_get_user(Some("ghost".to_string()), "ghost").await;
    assert_eq!(status, StatusCode::NOT_FOUND, "no row => 404, got {body}");
    assert_eq!(body["error"], "not found");
}

// ---------------------------------------------------------------------------
// Challenge-cookie extraction (plan §8.4): unique, non-empty, either name.

fn cookie_header(value: &str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        axum::http::header::COOKIE,
        axum::http::HeaderValue::from_str(value).expect("cookie header value"),
    );
    headers
}

#[test]
fn challenge_extraction_accepts_only_the_mode_correct_name() {
    // Production HTTPS honors the host-only __Host- name...
    let production = cookie_header("first=1; __Host-bcs_oauth_login=nonce-a; other=2");
    assert_eq!(
        extract_login_challenge(&production, true).ok().as_deref(),
        Some("nonce-a")
    );
    // ...and REJECTS the local-HTTP name: over production HTTPS a
    // Domain-scoped bcs_oauth_login cookie planted by a hostile subdomain
    // must not satisfy the browser binding (Login CSRF).
    let planted = cookie_header("bcs_oauth_login=subdomain-plant");
    assert_eq!(extract_login_challenge(&planted, true), Err(()));

    // Local plain-HTTP uses the separate non-__Host name...
    let local = cookie_header("bcs_oauth_login=nonce-b");
    assert_eq!(
        extract_login_challenge(&local, false).ok().as_deref(),
        Some("nonce-b")
    );
    // ...and never accepts a Secure __Host- cookie as its binding.
    let wrong_mode = cookie_header("__Host-bcs_oauth_login=nonce-b");
    assert_eq!(extract_login_challenge(&wrong_mode, false), Err(()));
}

#[test]
fn challenge_extraction_rejects_missing_duplicate_and_empty() {
    let none = cookie_header("bcs_session=abc");
    assert_eq!(extract_login_challenge(&none, true), Err(()));
    let empty = cookie_header("__Host-bcs_oauth_login=");
    assert_eq!(extract_login_challenge(&empty, true), Err(()));
    let mut duplicated = cookie_header("__Host-bcs_oauth_login=a");
    duplicated.append(
        axum::http::header::COOKIE,
        axum::http::HeaderValue::from_static("__Host-bcs_oauth_login=b"),
    );
    assert_eq!(extract_login_challenge(&duplicated, true), Err(()));
}

// ---------------------------------------------------------------------------
// Session-cookie presentation obligations (Task 10 review):
// missing -> None (idempotent boundary); EMPTY -> error, never a token.

#[test]
fn session_cookie_presentation_rules() {
    let missing = cookie_header("other=1");
    assert_eq!(presented_session_cookie(&missing), Ok(None));
    let present = cookie_header("bcs_session=abc; other=1");
    assert_eq!(
        presented_session_cookie(&present),
        Ok(Some("abc".to_string()))
    );
    let empty = cookie_header("bcs_session=");
    assert_eq!(presented_session_cookie(&empty), Err(()));
}

// ---------------------------------------------------------------------------
// Cookieinstruction encoding (plan §8.4 attributes).

#[test]
fn cookie_encoding_follows_the_pinned_contract() {
    use super::routes::encode_cookie_change as encode;
    let secure = encode(
        &BrowserCookieChange::SetLoginChallenge {
            nonce: "n".into(),
            expires_at: 1,
        },
        true,
    )
    .to_str()
    .expect("utf-8")
    .to_string();
    assert!(secure.starts_with("__Host-bcs_oauth_login=n;"));
    for attr in ["Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=300"] {
        assert!(secure.contains(attr), "missing {attr} in {secure}");
    }
    let local = encode(
        &BrowserCookieChange::SetLoginChallenge {
            nonce: "n".into(),
            expires_at: 1,
        },
        false,
    );
    let local = local.to_str().expect("utf-8").to_string();
    assert!(local.starts_with("bcs_oauth_login=n;"));
    assert!(!local.contains("Secure"));
    assert_eq!(
        encode(&BrowserCookieChange::ClearSession, true)
            .to_str()
            .expect("utf-8"),
        "bcs_session=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"
    );
}

// ---------------------------------------------------------------------------
// The cors-derived Origin gate (plan §8.3 compatibility tightening).

#[test]
fn origin_gate_exact_list_semantics() {
    use super::routes::cookie_unsafe_origin_allowed as allowed;
    let trusted = vec!["https://work.example".to_string()];
    let ok = cookie_header("x=1");
    let mut ok = ok;
    ok.insert(
        axum::http::header::ORIGIN,
        axum::http::HeaderValue::from_static("https://work.example"),
    );
    assert!(allowed(&ok, &trusted));
    for raw in ["https://evil.example", "https://work.example.evil.com", "null"] {
        let mut bad = HeaderMap::new();
        bad.insert(
            axum::http::header::ORIGIN,
            axum::http::HeaderValue::from_str(raw).expect("origin"),
        );
        assert!(!allowed(&bad, &trusted), "{raw} must not pass");
    }
    let no_origin = HeaderMap::new();
    assert!(!allowed(&no_origin, &trusted));
    assert!(
        !allowed(&ok, &[]),
        "no configured exact origins fails closed"
    );
    let mut duplicated = HeaderMap::new();
    duplicated.insert(
        axum::http::header::ORIGIN,
        axum::http::HeaderValue::from_static("https://work.example"),
    );
    duplicated.append(
        axum::http::header::ORIGIN,
        axum::http::HeaderValue::from_static("https://work.example"),
    );
    assert!(!allowed(&duplicated, &trusted), "duplicated Origin rejects");
}

// ---------------------------------------------------------------------------
// The thin handlers over a recording AuthService fake: the reply's cookie
// instructions must become SEPARATE appended Set-Cookie headers and the
// legacy text shapes must be preserved.

use std::sync::Mutex as StdMutex;

struct ScriptedAuthService {
    calls: StdMutex<Vec<&'static str>>,
}

impl ScriptedAuthService {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            calls: StdMutex::new(Vec::new()),
        })
    }
}

#[async_trait]
impl AuthService for ScriptedAuthService {
    async fn login_urls(
        &self,
        _request: BuildLoginUrls,
    ) -> AuthFlowReply<AuthProviderUrlList> {
        self.calls.lock().unwrap().push("login_urls");
        AuthFlowReply {
            result: Ok(AuthProviderUrlList {
                providers: vec![AuthProviderUrl {
                    name: "google".to_string(),
                    url: "https://provider.example/authorize?state=s".to_string(),
                }],
            }),
            cookie_changes: vec![BrowserCookieChange::SetLoginChallenge {
                nonce: "challenge-nonce".to_string(),
                expires_at: 1,
            }],
        }
    }

    async fn complete_login(
        &self,
        command: CompleteOAuthLogin,
    ) -> AuthFlowReply<AuthRedirectLocation> {
        self.calls
            .lock()
            .unwrap()
            .push(command.browser_binding.nonce.is_empty().then_some("bad").unwrap_or("complete_login"));
        AuthFlowReply {
            result: Ok(AuthRedirectLocation {
                location: "/after-login".to_string(),
            }),
            cookie_changes: vec![
                BrowserCookieChange::ClearLoginChallenge,
                BrowserCookieChange::SetSession {
                    token: "session-token".to_string(),
                    expires_at: 2,
                },
            ],
        }
    }

    async fn current_user(
        &self,
        _query: AuthenticatedUserQuery,
    ) -> Result<AuthUserInfo, bcs_service_api::application::v1::ApplicationError> {
        Err(bcs_service_api::application::v1::ApplicationError::internal("unused"))
    }

    async fn refresh_session(&self, session: PresentedSession) -> AuthFlowReply<SessionRenewalData> {
        self.calls
            .lock()
            .unwrap()
            .push(if session.token == "current-token" { "refresh" } else { "refresh-bad-token" });
        if session.token == "current-token" {
            AuthFlowReply {
                result: Ok(SessionRenewalData {}),
                cookie_changes: vec![BrowserCookieChange::SetSession {
                    token: "rotated-token".to_string(),
                    expires_at: 3,
                }],
            }
        } else {
            AuthFlowReply {
                result: Err(bcs_service_api::application::v1::ApplicationError::Unauthenticated),
                cookie_changes: Vec::new(),
            }
        }
    }

    async fn logout(
        &self,
        session: Option<PresentedSession>,
    ) -> AuthFlowReply<LogoutData> {
        match session {
            None => {
                self.calls.lock().unwrap().push("logout-idempotent");
                AuthFlowReply {
                    result: Ok(LogoutData {}),
                    cookie_changes: vec![BrowserCookieChange::ClearSession],
                }
            }
            Some(session) => {
                self.calls.lock().unwrap().push("logout-revoke");
                AuthFlowReply {
                    result: Ok(LogoutData {}),
                    cookie_changes: vec![BrowserCookieChange::ClearSession],
                }
            }
        }
    }
}

fn legacy_state(service: Arc<ScriptedAuthService>) -> Arc<OAuthRouteState> {
    Arc::new(OAuthRouteState::new(
        service,
        Arc::new(NoopUserIdentityPort),
        OAuthConfig {
            jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: false,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
        vec!["https://work.example".to_string()],
    ))
}

async fn response_parts(
    response: axum::response::Response,
) -> (
    StatusCode,
    Vec<String>,
    Option<String>,
    String,
) {
    let status = response.status();
    let cookies = response
        .headers()
        .get_all(axum::http::header::SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok().map(str::to_string))
        .collect();
    let location = response
        .headers()
        .get(axum::http::header::LOCATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let cache = response
        .headers()
        .get(axum::http::header::CACHE_CONTROL)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string)
        .unwrap_or_default();
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body");
    (status, cookies, location, cache)
}

#[tokio::test]
async fn url_handler_appends_the_challenge_cookie_and_keeps_the_legacy_json_shape() {
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let resp = auth_url_handler(State(state)).await.into_response();
    let (status, cookies, _, cache) = response_parts(resp).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cookies.len(), 1, "one SetLoginChallenge => one header");
    assert!(
        cookies[0].starts_with("__Host-bcs_oauth_login="),
        "HTTPS base keeps the host-only challenge despite cookie_secure=false: {}",
        cookies[0]
    );
    assert!(cookies[0].contains("Secure"));
    assert!(cookies[0].contains("Max-Age=300"));
    assert!(cookies[0].contains("HttpOnly"));
    assert_eq!(cache, "no-store");
    assert_eq!(*service.calls.lock().unwrap(), vec!["login_urls"]);
}

#[tokio::test]
async fn callback_success_sets_session_and_clears_challenge_as_separate_headers() {
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let headers = cookie_header("__Host-bcs_oauth_login=challenge-nonce");
    let resp = callback_handler(
        State(state),
        axum::extract::Path("google".to_string()),
        axum::extract::Query(CallbackParams {
            code: Some("code-1".to_string()),
            auth_code: None,
            state: "state-1".to_string(),
        }),
        headers,
    )
    .await
    .into_response();
    let (status, cookies, location, cache) = response_parts(resp).await;
    assert_eq!(status, StatusCode::FOUND);
    assert_eq!(location.as_deref(), Some("/after-login"));
    assert_eq!(cookies.len(), 2, "session set + challenge clear, appended");
    assert!(
        cookies.iter().any(|cookie| cookie.starts_with("bcs_session=session-token")),
        "the session install arrives as its own header: {cookies:?}"
    );
    assert!(
        cookies
            .iter()
            .any(|cookie| cookie.starts_with("__Host-bcs_oauth_login=;")
                && cookie.contains("Secure") && cookie.contains("Max-Age=0")),
        "the challenge clear arrives as its own header: {cookies:?}"
    );
    assert!(!cookies.iter().find(|cookie| cookie.starts_with("bcs_session="))
        .unwrap().contains("Secure"), "session cookie override is preserved");
    assert_eq!(cache, "no-store");
    assert_eq!(service.calls.lock().unwrap().as_slice(), ["complete_login"]);
}

/// Production-mode handlers must reject the LOCAL-HTTP challenge cookie
/// name (a subdomain-planted Domain cookie must not satisfy binding).
#[tokio::test]
async fn callback_rejects_the_wrong_transport_mode_cookie_name() {
    // HTTPS challenge mode is independent of the session cookie override.
    let service = ScriptedAuthService::new();
    let state = Arc::new(OAuthRouteState::new(
        service.clone(),
        Arc::new(NoopUserIdentityPort),
        OAuthConfig {
            jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: false,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
        vec!["https://work.example".to_string()],
    ));
    let headers = cookie_header("bcs_oauth_login=attacker-nonce");
    let resp = callback_handler(
        State(state),
        axum::extract::Path("google".to_string()),
        axum::extract::Query(CallbackParams {
            code: Some("code-1".to_string()),
            auth_code: None,
            state: "state-1".to_string(),
        }),
        headers,
    )
    .await
    .into_response();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST, "production rejects the local name");
    assert!(service.calls.lock().unwrap().is_empty(), "no service call may happen");

    // Local-HTTP config: cookie_secure = false; a __Host- name is equally
    // wrong there (its cookie cannot arrive intact, and the mode's one name
    // is the local one).
    let service = ScriptedAuthService::new();
    let state = Arc::new(OAuthRouteState::new(
        service.clone(),
        Arc::new(NoopUserIdentityPort),
        OAuthConfig {
            jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
            idle_timeout_minutes: 30,
            base_url: "http://127.0.0.1:21000".to_string(),
            cookie_secure: false,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
        vec![],
    ));
    let headers = cookie_header("__Host-bcs_oauth_login=wrong-mode-nonce");
    let resp = callback_handler(
        State(state),
        axum::extract::Path("google".to_string()),
        axum::extract::Query(CallbackParams {
            code: Some("code-1".to_string()),
            auth_code: None,
            state: "state-1".to_string(),
        }),
        headers,
    )
    .await
    .into_response();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST, "local mode rejects the __Host- name");
    assert!(service.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn callback_without_challenge_cookie_is_rejected_before_the_service() {
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let resp = callback_handler(
        State(state),
        axum::extract::Path("google".to_string()),
        axum::extract::Query(CallbackParams {
            code: Some("code-1".to_string()),
            auth_code: None,
            state: "state-1".to_string(),
        }),
        HeaderMap::new(),
    )
    .await
    .into_response();
    let (status, cookies, location, _) = response_parts(resp).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(cookies.is_empty());
    assert_eq!(location, None);
    assert!(
        service.calls.lock().unwrap().is_empty(),
        "no service call may happen without the browser binding"
    );
}

#[tokio::test]
async fn refresh_rotates_the_cookie_behind_the_trusted_origin_gate() {
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let mut headers = cookie_header("bcs_session=current-token");
    headers.insert(
        axum::http::header::ORIGIN,
        axum::http::HeaderValue::from_static("https://work.example"),
    );
    let (status, cookies, _, _) = response_parts(refresh_handler(State(state), headers).await.into_response()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cookies.len(), 1);
    assert!(cookies[0].starts_with("bcs_session=rotated-token"));
    assert!(cookies[0].contains("SameSite=Lax"));

    // Without the Origin, the same cookie request is forbidden.
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let headers = cookie_header("bcs_session=current-token");
    let (status, cookies, _, _) = response_parts(refresh_handler(State(state), headers).await.into_response()).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert!(cookies.is_empty());
    assert!(service.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn logout_rejects_empty_cookie_and_honors_the_cookie_less_boundary() {
    // EMPTY cookie = malformed credential, an error (never an empty token).
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let headers = cookie_header("bcs_session=");
    let (status, _, _, _) = response_parts(logout_handler(State(state), headers).await.into_response()).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(service.calls.lock().unwrap().is_empty());

    // Missing cookie = the valid idempotent boundary that still clears.
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let (status, cookies, _, _) =
        response_parts(logout_handler(State(state), HeaderMap::new()).await.into_response()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cookies.len(), 1);
    assert!(cookies[0].starts_with("bcs_session=;"));
    assert!(cookies[0].contains("Max-Age=0"));
    assert_eq!(service.calls.lock().unwrap().as_slice(), ["logout-idempotent"]);

    // With a cookie but no Origin: forbidden (cookie unsafe write).
    let service = ScriptedAuthService::new();
    let state = legacy_state(service.clone());
    let headers = cookie_header("bcs_session=current-token");
    let (status, _, _, _) =
        response_parts(logout_handler(State(state), headers).await.into_response()).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
}

// ---------------------------------------------------------------------------
// The identity-only mount: no login family route may run without the
// injected service.

#[tokio::test]
async fn identity_only_state_has_no_injected_login_service() {
    let state = OAuthRouteState::new_chain_only(
        Arc::new(NoopUserIdentityPort),
        chain_with(Some("u".into())),
    );
    assert!(state.require_auth_service().is_none());
    let (status, body) = run_current_user(chain_with(Some("u-1".into()))).await;
    assert_eq!(status, StatusCode::OK, "identity-only /auth/user still works, got {body}");
}

#[tokio::test]
async fn local_http_challenge_does_not_inherit_a_secure_session_override() {
    let service = ScriptedAuthService::new();
    let mut state = legacy_state(service.clone());
    let config = &mut Arc::get_mut(&mut state).expect("unshared state").config;
    config.base_url = "http://127.0.0.1:21000".to_string();
    config.cookie_secure = true;
    let (_, cookies, _, _) =
        response_parts(auth_url_handler(State(state.clone())).await.into_response()).await;
    assert!(cookies[0].starts_with("bcs_oauth_login="));
    assert!(!cookies[0].contains("Secure"));

    let response = callback_handler(
        State(state),
        axum::extract::Path("google".to_string()),
        axum::extract::Query(CallbackParams {
            code: Some("code-1".to_string()),
            auth_code: None,
            state: "state-1".to_string(),
        }),
        cookie_header("bcs_oauth_login=challenge-nonce"),
    ).await.into_response();
    let (status, cookies, _, _) = response_parts(response).await;
    assert_eq!(status, StatusCode::FOUND);
    assert!(cookies.iter().any(|cookie| cookie.starts_with("bcs_oauth_login=;")
        && !cookie.contains("Secure") && cookie.contains("Max-Age=0")));
    assert!(cookies.iter().any(|cookie| cookie.starts_with("bcs_session=")
        && cookie.contains("Secure")));
    assert_eq!(service.calls.lock().unwrap().as_slice(), ["login_urls", "complete_login"]);
}
