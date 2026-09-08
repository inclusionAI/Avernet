use super::*;
use async_trait::async_trait;
use axum::body::to_bytes;
use bcs_auth_api::{AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, UserIdentityInfo};
use bcs_test_support::{capture_request_logs, MockFailure, MockOAuthProvider};
use serde_json::Value;
use std::sync::Mutex;

const JWT_SECRET: &str = "private-oauth-diagnostic-secret-32-bytes";
const AUTH_CODE: &str = "private-oauth-diagnostic-code";
const USER_ID: &str = "oauth-diagnostic-user";

#[derive(Clone, Copy, Debug, PartialEq)]
enum Failure {
    Exchange,
    UserInfo,
    Ensure,
    UpdateToken,
    LookupToken,
    LookupUser,
}

struct FailingIdentity {
    failure: Failure,
    calls: Mutex<Vec<&'static str>>,
    bound_hash: Mutex<Option<String>>,
    writes: Mutex<Vec<(String, String, u64)>>,
}

impl FailingIdentity {
    fn new(failure: Failure) -> Self {
        Self {
            failure,
            calls: Mutex::new(Vec::new()),
            bound_hash: Mutex::new(None),
            writes: Mutex::new(Vec::new()),
        }
    }

    fn record(&self, call: &'static str, failure: Failure) -> Result<(), AuthError> {
        self.calls.lock().unwrap().push(call);
        if self.failure == failure {
            Err(AuthError::LookupFailed("identity store unavailable".into()))
        } else {
            Ok(())
        }
    }

    fn info() -> UserIdentityInfo {
        UserIdentityInfo {
            user_id: USER_ID.into(),
            auth_source: "google".into(),
            user_name: Some("Current Name".into()),
            external_user_name: None,
            avatar: None,
        }
    }
}

#[async_trait]
impl UserIdentityPort for FailingIdentity {
    async fn ensure_identity(
        &self, source: &str, external_id: &str, name: Option<&str>,
        avatar: Option<&str>, env: &str,
    ) -> Result<String, AuthError> {
        assert_eq!((source, external_id, name, avatar, env),
            ("google", "external-42", Some("Mock User"), None, "test"));
        self.record("ensure", Failure::Ensure)?;
        Ok(USER_ID.into())
    }

    async fn lookup_by_user_id(&self, _: &str, _: &str) -> Result<Option<String>, AuthError> {
        panic!("OAuth flows must use the full identity lookup contract")
    }

    async fn get_identity_by_token(&self, token_hash: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        assert_eq!(self.bound_hash.lock().unwrap().as_deref(), Some(token_hash));
        self.record("lookup_token", Failure::LookupToken)?;
        Ok(Some(Self::info()))
    }

    async fn get_identity_by_user_id(&self, user_id: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        assert_eq!(user_id, USER_ID);
        self.record("lookup_user", Failure::LookupUser)?;
        Ok(Some(Self::info()))
    }

    async fn update_token(&self, user_id: &str, hash: &str, expires: u64) -> Result<(), AuthError> {
        assert_eq!(user_id, USER_ID);
        self.writes.lock().unwrap().push((user_id.into(), hash.into(), expires));
        self.record("update_token", Failure::UpdateToken)?;
        *self.bound_hash.lock().unwrap() = Some(hash.into());
        Ok(())
    }
}

fn state(failure: Failure) -> (Arc<OAuthRouteState>, Arc<FailingIdentity>) {
    let port = Arc::new(FailingIdentity::new(failure));
    let provider = MockOAuthProvider::new("google", "external-42").with_failure(match failure {
        Failure::Exchange => MockFailure::Exchange,
        Failure::UserInfo => MockFailure::UserInfo,
        _ => MockFailure::None,
    });
    let providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::from([
        ("google".into(), Arc::new(provider) as Arc<dyn OAuthProvider>),
    ]);
    let state = OAuthRouteState::new(JWT_SECRET, port.clone(), providers, OAuthConfig {
        jwt_secret: JWT_SECRET.into(),
        idle_timeout_minutes: 30,
        base_url: "https://bcs.example.com".into(),
        cookie_secure: true,
        env: "test".into(),
        success_redirect_path: "/".into(),
    }, None);
    (Arc::new(state), port)
}

fn warning<'a>(events: &'a [Value], request_id: &str, message: &str) -> &'a Value {
    let matching: Vec<_> = events.iter().filter(|event| event["fields"]["message"] == message).collect();
    assert_eq!(matching.len(), 1, "expected one {message}: {events:?}");
    let event = matching[0];
    assert_eq!(event["level"], "WARN");
    assert_eq!(event["fields"]["request_id"], request_id);
    event
}

fn assert_no_credentials(events: &[Value], secrets: &[&str]) {
    let logs = serde_json::to_string(events).unwrap();
    for secret in secrets {
        assert!(!logs.contains(secret), "OAuth credentials must not appear in diagnostic logs");
    }
}

async fn assert_failure_response(response: axum::response::Response, status: StatusCode, body: &str) {
    assert_eq!(response.status(), status);
    assert!(!response.headers().contains_key("set-cookie"), "failed authentication must not issue a session");
    assert!(!response.headers().contains_key("location"), "failed authentication must not redirect as success");
    let bytes = to_bytes(response.into_body(), 4096).await.unwrap();
    assert_eq!(std::str::from_utf8(&bytes).unwrap(), body);
}

fn expected_calls(failure: Failure) -> Vec<&'static str> {
    match failure {
        Failure::Exchange | Failure::UserInfo => vec![],
        Failure::Ensure => vec!["ensure"],
        Failure::UpdateToken => vec!["ensure", "update_token"],
        _ => panic!("expected login failure"),
    }
}

const LOGIN_FAILURES: [(Failure, &str, &str, &str); 4] = [
    (Failure::Exchange, "OAuth token exchange failed", "token_exchange_failed", "token exchange failed"),
    (Failure::UserInfo, "OAuth userinfo failed", "userinfo_request_failed", "userinfo request failed"),
    (Failure::Ensure, "ensure_identity failed", "internal_error", "identity creation failed"),
    (Failure::UpdateToken, "update_token failed; aborting login", "internal_error", "session creation failed"),
];

#[tokio::test]
async fn service_login_failures_keep_request_id_and_preserve_application_errors() {
    for (failure, message, code, public_message) in LOGIN_FAILURES {
        let (state, port) = state(failure);
        let csrf = state.state_store.generate("google").await;
        let request_id = format!("oauth-service-login-{failure:?}");
        let request = CompleteOAuthLogin {
            provider: "google".into(), code: Some(AUTH_CODE.into()), auth_code: None,
            state: csrf.clone(), callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".into(),
        };
        let (result, logs) = capture_request_logs(&request_id, state.complete_login(request.clone())).await;
        let error = result.unwrap_err();
        assert_eq!(error.code(), code);
        match error {
            ApplicationError::BadGateway { message, .. } | ApplicationError::Internal(message) => {
                assert_eq!(message, public_message);
            }
            other => panic!("unexpected application error: {other}"),
        }
        let event = warning(&logs, &request_id, message);
        assert!(event["fields"]["error"].as_str().is_some_and(|value| !value.is_empty()));
        if matches!(failure, Failure::Exchange | Failure::UserInfo) {
            assert_eq!(event["fields"]["provider"], "google");
        }
        assert_eq!(*port.calls.lock().unwrap(), expected_calls(failure));
        assert!(port.bound_hash.lock().unwrap().is_none());
        assert_no_credentials(&logs, &[AUTH_CODE, JWT_SECRET, &csrf]);
        assert_eq!(state.complete_login(request).await.unwrap_err().code(), "invalid_state",
            "a failed login must still consume the one-use CSRF state");
    }
}

#[tokio::test]
async fn callback_dependency_failures_keep_request_id_and_do_not_issue_cookies() {
    for (failure, message, _, body) in LOGIN_FAILURES {
        let (state, port) = state(failure);
        let csrf = state.state_store.generate("google").await;
        let request_id = format!("oauth-callback-{failure:?}");
        let (response, logs) = capture_request_logs(&request_id, callback_handler(
            State(state), Path("google".into()), axum::extract::Query(CallbackParams {
                code: Some(AUTH_CODE.into()), auth_code: None, state: csrf.clone(),
            }),
        )).await;
        assert_failure_response(response.into_response(), StatusCode::INTERNAL_SERVER_ERROR, body).await;
        let event = warning(&logs, &request_id, message);
        assert!(event["fields"]["error"].as_str().is_some_and(|value| !value.is_empty()));
        assert_eq!(*port.calls.lock().unwrap(), expected_calls(failure));
        assert!(port.bound_hash.lock().unwrap().is_none());
        assert_no_credentials(&logs, &[AUTH_CODE, JWT_SECRET, &csrf]);
    }
}

#[tokio::test]
async fn callback_validation_failures_identify_the_request_before_any_identity_write() {
    for (case, message, body) in [
        ("state", "OAuth callback: invalid state", "invalid state"),
        ("provider", "OAuth callback: provider mismatch", "provider mismatch"),
        ("code", "OAuth callback: missing code or auth_code", "missing code or auth_code"),
    ] {
        let (state, port) = state(Failure::Ensure);
        let csrf = match case {
            "state" => "private-invalid-csrf-state".to_string(),
            "provider" => state.state_store.generate("github").await,
            _ => state.state_store.generate("google").await,
        };
        let request_id = format!("oauth-callback-invalid-{case}");
        let (response, logs) = capture_request_logs(&request_id, callback_handler(
            State(state), Path("google".into()), axum::extract::Query(CallbackParams {
                code: (case != "code").then(|| AUTH_CODE.into()), auth_code: None, state: csrf.clone(),
            }),
        )).await;
        assert_failure_response(response.into_response(), StatusCode::BAD_REQUEST, body).await;
        let event = warning(&logs, &request_id, message);
        if case == "provider" {
            assert_eq!(event["fields"]["expected"], "github");
            assert_eq!(event["fields"]["got"], "google");
        }
        assert!(port.calls.lock().unwrap().is_empty());
        assert_no_credentials(&logs, &[AUTH_CODE, JWT_SECRET, &csrf]);
    }
}

fn session(state: &OAuthRouteState, port: &FailingIdentity) -> (String, HeaderMap, RequestAuthHeaders) {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
    let jwt = state.jwt_service.sign(&Claims {
        sub: USER_ID.into(), src: "google".into(), iat: now - 300, exp: now + 3600,
        name: Some("Previous Name".into()),
    }).unwrap();
    *port.bound_hash.lock().unwrap() = Some(bcs_jwt::token_hash(&jwt));
    let cookie = format!("bcs_session={jwt}");
    let mut headers = HeaderMap::new();
    headers.insert(axum::http::header::COOKIE, cookie.parse().unwrap());
    let auth = RequestAuthHeaders { authorization: None, cookie: Some(cookie), forwarded_headers: vec![] };
    (jwt, headers, auth)
}

#[tokio::test]
async fn refresh_failures_preserve_the_existing_binding_and_correlate_both_entry_points() {
    for failure in [Failure::LookupToken, Failure::UpdateToken] {
        for service in [false, true] {
            let (state, port) = state(failure);
            let (jwt, headers, auth) = session(&state, &port);
            let original_hash = bcs_jwt::token_hash(&jwt);
            let request_id = format!("oauth-refresh-{failure:?}-service-{service}");
            let logs = if service {
                let (result, logs) = capture_request_logs(&request_id, state.refresh_session(RefreshSession { headers: auth })).await;
                let expected = if failure == Failure::LookupToken { "identity lookup failed" } else { "session renewal failed" };
                assert!(matches!(result, Err(ApplicationError::Internal(ref message)) if message == expected));
                logs
            } else {
                let (response, logs) = capture_request_logs(&request_id, refresh_handler(State(state), headers)).await;
                let body = if failure == Failure::LookupToken { "internal error" } else { "session renewal failed" };
                assert_failure_response(response.into_response(), StatusCode::INTERNAL_SERVER_ERROR, body).await;
                logs
            };
            let message = if failure == Failure::LookupToken { "refresh: identity lookup failed" } else { "refresh: update_token failed" };
            warning(&logs, &request_id, message);
            assert_eq!(port.bound_hash.lock().unwrap().as_deref(), Some(original_hash.as_str()));
            if failure == Failure::LookupToken {
                assert_eq!(*port.calls.lock().unwrap(), vec!["lookup_token"]);
                assert!(port.writes.lock().unwrap().is_empty());
            } else {
                assert_eq!(*port.calls.lock().unwrap(), vec!["lookup_token", "update_token"]);
                let writes = port.writes.lock().unwrap();
                assert_eq!(writes.len(), 1);
                assert_eq!(writes[0].1.len(), 64, "only a fingerprint may be persisted");
                assert_ne!(writes[0].1, original_hash, "renewal must generate a new session fingerprint");
                assert!(writes[0].2 > 0);
            }
            assert_no_credentials(&logs, &[&jwt, JWT_SECRET, &original_hash]);
        }
    }
}

#[tokio::test]
async fn logout_revocation_failure_is_logged_while_both_entry_points_clear_the_cookie() {
    for service in [false, true] {
        let (state, port) = state(Failure::UpdateToken);
        let (jwt, headers, auth) = session(&state, &port);
        let request_id = format!("oauth-logout-service-{service}");
        let (cookie, logs) = if service {
            let (result, logs) = capture_request_logs(&request_id, state.logout(LogoutSession { headers: auth })).await;
            (result.unwrap().set_cookie, logs)
        } else {
            let (response, logs) = capture_request_logs(&request_id, logout_handler(State(state), headers)).await;
            let response = response.into_response();
            assert_eq!(response.status(), StatusCode::OK);
            (response.headers()["set-cookie"].to_str().unwrap().to_owned(), logs)
        };
        assert_eq!(cookie, clear_session_cookie(true));
        assert!(cookie.contains("Max-Age=0"));
        assert_eq!(*port.writes.lock().unwrap(), vec![(USER_ID.into(), "".into(), 0)]);
        let event = warning(&logs, &request_id, "logout: token revocation failed");
        assert_eq!(event["fields"]["user_id"], USER_ID);
        assert_no_credentials(&logs, &[&jwt, JWT_SECRET]);
    }
}

struct FailingAuthPlugin;

#[async_trait]
impl AuthPlugin for FailingAuthPlugin {
    fn can_authenticate(&self, _: &HeaderMap) -> bool { true }
    async fn authenticate(&self, _: &HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
        Err(AuthError::LookupFailed("authentication store unavailable".into()))
    }
    fn priority(&self) -> u8 { 1 }
    fn name(&self) -> &'static str { "diagnostic-failing-auth" }
}

#[tokio::test]
async fn current_user_dependency_failure_correlates_chain_and_endpoint_warnings() {
    for service in [false, true] {
        let state = Arc::new(OAuthRouteState::new_chain_only(
            Arc::new(FailingIdentity::new(Failure::LookupUser)),
            Arc::new(AuthPluginChain::new(vec![Box::new(FailingAuthPlugin)])),
        ));
        let request_id = format!("oauth-current-user-service-{service}");
        let logs = if service {
            let (result, logs) = capture_request_logs(&request_id, state.current_user(ReadCurrentUser {
                headers: RequestAuthHeaders { authorization: None, cookie: None, forwarded_headers: vec![] },
            })).await;
            assert!(matches!(result, Err(ApplicationError::Internal(ref message)) if message == "auth chain failed"));
            logs
        } else {
            let (response, logs) = capture_request_logs(&request_id, current_user_handler(State(state), HeaderMap::new())).await;
            assert_failure_response(response.into_response(), StatusCode::INTERNAL_SERVER_ERROR, "internal error").await;
            logs
        };
        let chain = warning(&logs, &request_id, "auth: plugin failed");
        assert_eq!(chain["fields"]["plugin"], "diagnostic-failing-auth");
        assert_eq!(chain["fields"]["outcome"], "error");
        warning(&logs, &request_id, if service { "auth chain failed in OpenAPI auth user" } else { "auth chain failed in /auth/user" });
    }
}

#[tokio::test]
async fn get_user_identity_failure_keeps_request_id_and_returns_a_generic_error() {
    let (state, port) = state(Failure::LookupUser);
    let (jwt, headers, _) = session(&state, &port);
    let request_id = "oauth-get-user-identity-failure";
    let (response, logs) = capture_request_logs(request_id, get_user_handler(
        State(state), Path(USER_ID.into()), headers,
    )).await;
    assert_failure_response(response.into_response(), StatusCode::INTERNAL_SERVER_ERROR, "internal error").await;
    warning(&logs, request_id, "get_identity_by_user_id failed");
    assert_eq!(*port.calls.lock().unwrap(), vec!["lookup_user"]);
    assert_no_credentials(&logs, &[&jwt, JWT_SECRET]);
}
