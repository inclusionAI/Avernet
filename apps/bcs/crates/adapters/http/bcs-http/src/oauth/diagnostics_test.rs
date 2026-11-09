//! OAuth diagnostics tests (Task 11 shape): the legacy entry's handlers
//! are thin adapters, so their logging obligations are (a) every
//! rejection is correlated with the request id and carries a STABLE
//! category — never the raw credential material, the cookie/nonce/state
//! values, or third-party text; (b) the retained `/auth/user` chain and
//! `/auth/user/{user_id}` identity readers keep their failure
//! correlation. The flow business-failure taxonomy itself is now owned
//! and logged by the shared application service (covered by
//! `bcs-app-auth` log redaction and the bootstrap
//! `auth_log_redaction.rs` E2E capture) — those suites replace the old
//! per-port failure-injection cases removed with the in-adapter
//! business logic.
use super::*;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use bcs_test_support::NoopUserIdentityPort;

use async_trait::async_trait;
use axum::body::to_bytes;
use bcs_auth_api::{AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, UserIdentityInfo};
use bcs_test_support::capture_request_logs;
use serde_json::Value;
use std::sync::Mutex as StdMutex;

const JWT_SECRET: &str = "private-oauth-diagnostic-secret-32-bytes";

/// A chain plugin that always fails with a lookup error.
struct FailingAuthPlugin;

#[async_trait]
impl AuthPlugin for FailingAuthPlugin {
    fn can_authenticate(&self, _: &HeaderMap) -> bool {
        true
    }
    async fn authenticate(&self, _: &HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
        Err(AuthError::LookupFailed("authentication store unavailable".into()))
    }
    fn priority(&self) -> u8 {
        1
    }
    fn name(&self) -> &'static str {
        "diagnostic-failing-auth"
    }
}

#[tokio::test]
async fn current_user_dependency_failure_correlates_chain_and_endpoint_warnings() {
    let state = Arc::new(OAuthRouteState::new_chain_only(
        Arc::new(bcs_test_support::NoopUserIdentityPort),
        Arc::new(AuthPluginChain::new(vec![Box::new(FailingAuthPlugin)])),
    ));
    let request_id = "oauth-current-user-chain-failure";
    let (logs, status) = {
        let (response, logs) = capture_request_logs(
            request_id,
            current_user_handler(State(state), HeaderMap::new()),
        )
        .await;
        (logs, response.into_response().status())
    };
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    let chain = warning(&logs, request_id, "auth: plugin failed");
    assert_eq!(chain["fields"]["plugin"], "diagnostic-failing-auth");
    assert_eq!(chain["fields"]["outcome"], "error");
    warning(&logs, request_id, "auth chain failed in /auth/user");
}

/// The `/auth/user/{user_id}` identity-reader failure keeps its request-id
/// correlation and generic public error. The caller resolves through the
/// injected auth chain (the strict delegated verification); the display
/// lookup then fails on the identity port.
struct FailingIdentity;

#[async_trait]
impl UserIdentityPort for FailingIdentity {
    async fn ensure_identity(
        &self,
        _: &str,
        _: &str,
        _: Option<&str>,
        _: Option<&str>,
        _: &str,
    ) -> Result<String, AuthError> {
        Err(AuthError::LookupFailed("identity store unavailable".into()))
    }
    async fn lookup_by_user_id(&self, _: &str, _: &str) -> Result<Option<String>, AuthError> {
        Ok(None)
    }
    async fn get_identity_by_token(&self, _: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }
    async fn get_identity_by_user_id(&self, _: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Err(AuthError::LookupFailed("identity store unavailable".into()))
    }
}

/// A chain plugin that always yields a principal for the fixed subject —
/// standing in for a strictly verified `bcs_session` login so the identity
/// lookup failure (not the verification) is the path under test.
struct FixedVerifiedUserPlugin {
    user_id: String,
}

#[async_trait]
impl AuthPlugin for FixedVerifiedUserPlugin {
    fn can_authenticate(&self, _: &HeaderMap) -> bool {
        true
    }
    async fn authenticate(&self, _: &HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
        let mut principal = AuthPrincipal::new(bcs_auth_api::AuthSource::Local);
        principal.user_id = Some(self.user_id.clone());
        Ok(Some(principal))
    }
    fn priority(&self) -> u8 {
        10
    }
    fn name(&self) -> &'static str {
        "diagnostic-verified-user"
    }
}

fn get_user_identity_failure_state() -> Arc<OAuthRouteState> {
    Arc::new(OAuthRouteState::new_chain_only(
        Arc::new(FailingIdentity),
        Arc::new(AuthPluginChain::new(vec![Box::new(
            FixedVerifiedUserPlugin {
                user_id: "oauth-diagnostic-user".to_string(),
            },
        )])),
    ))
}

#[tokio::test]
async fn get_user_identity_failure_keeps_request_id_and_returns_a_generic_error() {
    let state = get_user_identity_failure_state();
    let request_id = "oauth-get-user-identity-failure";
    let (response, logs) = capture_request_logs(
        request_id,
        get_user_handler(
            State(state),
            axum::extract::Path("oauth-diagnostic-user".into()),
            HeaderMap::new(),
        ),
    )
    .await;
    let response = response.into_response();
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    warning(&logs, request_id, "get_identity_by_user_id failed");
    assert_no_credentials(&logs, &[JWT_SECRET]);
}

// ---------------------------------------------------------------------------
// Thin-adapter rejections: category-only warnings, request-id correlated,
// credentials never formatted.

use bcs_service_api::application::v1::{
    ApplicationError, AuthFlowReply, AuthRedirectLocation, AuthService, AuthProviderUrlList,
    AuthenticatedUserQuery, AuthUserInfo, BuildLoginUrls, CompleteOAuthLogin, LogoutData,
    PresentedSession, SessionRenewalData,
};

struct CountingService {
    calls: StdMutex<&'static str>,
}

fn scripted_service() -> (Arc<CountingService>, ()) {
    (Arc::new(CountingService { calls: StdMutex::new("") }), ())
}

#[async_trait]
impl AuthService for CountingService {
    async fn login_urls(&self, _request: BuildLoginUrls) -> AuthFlowReply<AuthProviderUrlList> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }
    async fn complete_login(
        &self,
        _command: CompleteOAuthLogin,
    ) -> AuthFlowReply<AuthRedirectLocation> {
        *self.calls.lock().unwrap() = "complete_login";
        AuthFlowReply {
            result: Ok(AuthRedirectLocation {
                location: "/".to_string(),
            }),
            cookie_changes: Vec::new(),
        }
    }
    async fn current_user(
        &self,
        _query: AuthenticatedUserQuery,
    ) -> Result<AuthUserInfo, ApplicationError> {
        Err(ApplicationError::internal("unused"))
    }
    async fn refresh_session(&self, session: PresentedSession) -> AuthFlowReply<SessionRenewalData> {
        *self.calls.lock().unwrap() = "refresh";
        let _ = session;
        AuthFlowReply {
            result: Err(ApplicationError::Unauthenticated),
            cookie_changes: Vec::new(),
        }
    }
    async fn logout(&self, session: Option<PresentedSession>) -> AuthFlowReply<LogoutData> {
        *self.calls.lock().unwrap() = "logout";
        let _ = session;
        AuthFlowReply {
            result: Ok(LogoutData {}),
            cookie_changes: Vec::new(),
        }
    }
}

fn diagnostic_state() -> Arc<OAuthRouteState> {
    let (service, ()) = scripted_service();
    Arc::new(OAuthRouteState::new(
        service,
        Arc::new(bcs_test_support::NoopUserIdentityPort),
        OAuthConfig {
            jwt_secret: JWT_SECRET.to_string(),
            idle_timeout_minutes: 30,
            base_url: "https://bcs.example.com".to_string(),
            cookie_secure: true,
            env: "test".to_string(),
            success_redirect_path: "/".to_string(),
        },
        None,
        Vec::new(),
    ))
}

fn warning<'a>(events: &'a [Value], request_id: &str, message: &str) -> &'a Value {
    let matching: Vec<_> = events
        .iter()
        .filter(|event| event["fields"]["message"] == message)
        .collect();
    assert_eq!(matching.len(), 1, "expected one {message}: {events:?}");
    let event = matching[0];
    assert_eq!(event["level"], "WARN");
    assert_eq!(event["fields"]["request_id"], request_id);
    event
}

fn assert_no_credentials(events: &[Value], secrets: &[&str]) {
    let logs = serde_json::to_string(events).unwrap();
    for secret in secrets {
        assert!(
            !logs.contains(secret),
            "OAuth credentials must not appear in diagnostic logs"
        );
    }
}

#[tokio::test]
async fn callback_binding_rejection_logs_category_only_with_the_request_id() {
    let state = diagnostic_state();
    let request_id = "oauth-callback-binding-rejection";
    let (response, logs) = capture_request_logs(
        request_id,
        callback_handler(
            State(state),
            axum::extract::Path("google".into()),
            axum::extract::Query(CallbackParams {
                code: Some("private-oauth-diagnostic-code".into()),
                auth_code: None,
                state: "private-callback-state".into(),
            }),
            HeaderMap::new(),
        ),
    )
    .await;
    assert_eq!(response.into_response().status(), StatusCode::BAD_REQUEST);
    let event = warning(&logs, request_id, "OAuth callback: browser challenge cookie missing or invalid");
    assert_eq!(event["fields"]["category"], "invalid_state");
    assert_no_credentials(
        &logs,
        &["private-oauth-diagnostic-code", "private-callback-state", JWT_SECRET],
    );
}

#[tokio::test]
async fn refresh_origin_rejection_and_empty_cookie_rejection_stay_correlated_and_redacted() {
    // No Origin on a cookie-bearing unsafe write.
    let state = diagnostic_state();
    let request_id = "oauth-refresh-origin-rejection";
    let mut headers = HeaderMap::new();
    headers.insert(axum::http::header::COOKIE, "bcs_session=private-refresh-jwt".parse().unwrap());
    let (response, logs) = capture_request_logs(request_id, refresh_handler(State(state), headers)).await;
    assert_eq!(response.into_response().status(), StatusCode::FORBIDDEN);
    warning(&logs, request_id, "refresh rejected: cookie origin not trusted");
    assert_no_credentials(&logs, &["private-refresh-jwt", JWT_SECRET]);

    // EMPTY cookie value is a malformed credential message, redacted too.
    let state = diagnostic_state();
    let request_id = "oauth-logout-empty-cookie";
    let mut headers = HeaderMap::new();
    headers.insert(axum::http::header::COOKIE, "bcs_session=".parse().unwrap());
    let (response, logs) = capture_request_logs(request_id, logout_handler(State(state), headers)).await;
    assert_eq!(response.into_response().status(), StatusCode::UNAUTHORIZED);
    warning(&logs, request_id, "logout rejected: empty session cookie");
    assert_no_credentials(&logs, &[JWT_SECRET]);
}
