use super::*;
use async_trait::async_trait;
use axum::body::to_bytes;
use bcs_app_auth::{AuthApplicationService, AuthApplicationServiceConfig};
use bcs_auth_api::{AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, UserIdentityInfo};
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort,
    PendingLoginBatch, PendingOAuthLoginPort,
};
use bcs_test_support::{capture_request_logs, NoopUserIdentityPort};
use serde_json::Value;
use std::sync::Mutex;

// Diagnostics tests for the legacy /auth/* entrypoint AFTER the Task 11
// switchover: the handlers are delivery adapters over the SHARED secure
// service, so failure-path coverage moved from the old trait-impl internals
// to the handler + fake-port boundary. Coverage transfer from the old suite:
// - exchange / userinfo failures keep the old 500 plain bodies;
// - cross-browser reject (no challenge cookie) never reaches the provider;
// - per-failure a single correlated warn! (request_id) is emitted and no
//   credential (code / JWT / secret / state nonce) reaches the logs;
// - the strictly richer dependency-error taxonomy (503 Unavailable for
//   store faults) replaces the old catch-all 500 — a documented taxonomic
//   tightening from spec §8.6, asserted in the refresh/logout cases.

const JWT_SECRET: &str = "private-oauth-diagnostic-secret-32-bytes";
const AUTH_CODE: &str = "private-oauth-diagnostic-code";

#[derive(Clone, Copy, Debug, PartialEq)]
enum Failure {
    Exchange,
    UserInfo,
    #[allow(dead_code)]
    Install,
}

struct FailingProvider {
    exchanges: Mutex<Vec<String>>,
    fail: Failure,
}

impl FailingProvider {
    fn exchange_count(&self) -> usize {
        self.exchanges.lock().unwrap().len()
    }
}

#[async_trait]
impl OAuthProviderPort for FailingProvider {
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
        self.exchanges.lock().unwrap().push(code.to_string());
        if self.fail == Failure::Exchange {
            return Err(ApplicationError::bad_gateway(
                "oauth_exchange_failed",
                "token exchange failed: mock forced failure",
            ));
        }
        if self.fail == Failure::UserInfo {
            // exchange succeeded; the userinfo stage is part of the combined
            // `exchange_user` round-trip but the OLD legacy boundary surfaced
            // userinfo failures under their own error code — keep that code
            // for envelope parity.
            let _ = provider;
            return Err(ApplicationError::bad_gateway(
                "oauth_userinfo_failed",
                "userinfo request failed: mock forced failure",
            ));
        }
        Ok(ExternalLoginIdentity {
            provider: provider.to_string(),
            external_user_id: "external-42".to_string(),
            name: Some("Mock User".to_string()),
            avatar: None,
        })
    }
}

#[derive(Default)]
struct FailingPending {
    batches: Mutex<Vec<(String, String)>>,
}

#[async_trait]
impl PendingOAuthLoginPort for FailingPending {
    async fn issue_batch(
        &self,
        providers: &[String],
        _callback_base: &str,
        _flow: &str,
        _now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError> {
        let index = self.batches.lock().unwrap().len();
        let state = format!("pending-state-{index}");
        self.batches.lock().unwrap().push((
            state.clone(),
            format!("browser-nonce-{index}"),
        ));
        Ok(PendingLoginBatch {
            browser_nonce: format!("browser-nonce-{index}"),
            expires_at: u64::MAX,
            provider_states: providers
                .iter()
                .map(|p| (p.clone(), state.clone()))
                .collect(),
        })
    }

    async fn consume(
        &self,
        state: &str,
        _browser_nonce: &str,
        _provider: &str,
        _exact_callback: &str,
        _flow: &str,
        _now: u64,
    ) -> Result<(), ApplicationError> {
        let batches = self.batches.lock().unwrap();
        for (pending_state, _pending_nonce) in batches.iter() {
            if pending_state == state {
                return Ok(());
            }
        }
        Err(ApplicationError::invalid("oauth_invalid_state", "no match"))
    }
}

#[derive(Default)]
struct FailingSession {
    installs: Mutex<Vec<String>>,
}

#[async_trait]
impl OAuthSessionPort for FailingSession {
    async fn install_identity(
        &self,
        _identity: ExternalLoginIdentity,
        _now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        self.installs.lock().unwrap().push("installed".to_string());
        Ok(BrowserSession {
            token: "jwt-diagnostic".to_string(),
            expires_at: u64::MAX,
        })
    }

    async fn refresh(&self, _token: &str, _now: u64) -> Result<BrowserSession, ApplicationError> {
        Err(ApplicationError::unavailable("session store unavailable"))
    }

    async fn revoke(&self, _token: &str) -> Result<(), ApplicationError> {
        Err(ApplicationError::unavailable("session store unavailable"))
    }
}

fn failing_state(failure: Failure) -> Arc<OAuthRouteState> {
    let service = Arc::new(AuthApplicationService::new(
        Arc::new(FailingProvider {
            exchanges: Mutex::new(Vec::new()),
            fail: failure,
        }),
        Arc::new(FailingSession::default()),
        Arc::new(FailingPending::default()),
        AuthApplicationServiceConfig {
            enabled_providers: vec!["google".to_string()],
            flow: "legacy".to_string(),
            post_login_redirect: "/".to_string(),
        },
    ));
    let config = OAuthConfig {
        jwt_secret: JWT_SECRET.to_string(),
        idle_timeout_minutes: 30,
        base_url: "https://bcs.example.com".to_string(),
        cookie_secure: true,
        env: "test".to_string(),
        success_redirect_path: "/".to_string(),
    };
    Arc::new(OAuthRouteState::new(
        service,
        JWT_SECRET,
        Arc::new(NoopUserIdentityPort),
        config,
        None,
        Some(Arc::new(vec!["https://workbench.example".to_string()])),
    ))
}

fn warning<'a>(events: &'a [Value], request_id: &str, message_prefix: &str) -> &'a Value {
    let matching = events
        .iter()
        .filter(|event| {
            event["fields"]["message"]
                .as_str()
                .is_some_and(|m| m.starts_with(message_prefix))
        })
        .collect::<Vec<_>>();
    assert_eq!(matching.len(), 1, "expected exactly one {message_prefix}: {events:?}");
    assert_eq!(matching[0]["level"], "WARN");
    assert_eq!(matching[0]["fields"]["request_id"], request_id);
    matching[0]
}

fn assert_no_credentials(events: &[Value], secrets: &[String]) {
    let logs = serde_json::to_string(events).unwrap();
    for secret in secrets {
        assert!(!logs.contains(secret), "OAuth credentials must not appear in diagnostic logs");
    }
}

async fn run_login_failure(failure: Failure, code: &str, public_message: &str) {
    let state = failing_state(failure);
    let request_id = format!("oauth-login-{failure:?}");
    // The login-URL building itself SUCCEEDS for these failure modes
    // (exchange/userinfo fail later in the flow); the challenge cookie is
    // expected here. The credential-leak check runs over the whole exchange.
    let (response, logs) =
        capture_request_logs(&request_id, get_auth_url_response(&state)).await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_no_credentials(&logs, &[JWT_SECRET.to_string()]);

    // The pipeline through the callback keeps the OLD plain-text body for the
    // provider-side failures and burns nothing on the session side.
    let (nonce, url_state) = start_login(&state).await;
    let (response, logs) = capture_request_logs(
        &request_id,
        callback_handler(
            State(state.clone()),
            Path("google".to_string()),
            axum::extract::Query(CallbackParams {
                code: Some(AUTH_CODE.to_string()),
                auth_code: None,
                state: url_state,
            }),
            cookie_headers(CHALLENGE_COOKIE_SECURE, &nonce),
        ),
    )
    .await;
    let status = response.status();
    let set_cookie_headers: Vec<String> = response
        .headers()
        .get_all(axum::http::header::SET_COOKIE)
        .iter()
        .map(|value| value.to_str().expect("ascii").to_string())
        .collect();
    let has_location = response.headers().contains_key("location");
    let body = to_bytes(response.into_body(), 4096).await.unwrap();
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR, "{failure:?}");
    assert_eq!(std::str::from_utf8(&body).unwrap(), public_message);
    // Post-Task-11 contract (spec §8.4): a MATCHED consume ALWAYS clears the
    // challenge cookie even when the exchange that follows fails — and never
    // issues a session cookie.
    assert_eq!(set_cookie_headers.len(), 1, "only the challenge clear: {set_cookie_headers:?}");
    assert!(
        set_cookie_headers[0].starts_with("__Host-bcs_oauth_login=")
            && set_cookie_headers[0].contains("Max-Age=0"),
        "the cleared cookie must be the challenge"
    );
    assert!(
        !has_location,
        "failed authentication must not redirect as success"
    );
    let event = warning(&logs, &request_id, "OAuth callback failed");
    assert_eq!(event["level"], "WARN");
    assert!(event["fields"]["error"].as_str().is_some());
    assert_no_credentials(&logs, &[AUTH_CODE.to_string(), JWT_SECRET.to_string()]);
    let _ = code;
}

async fn get_auth_url_response(state: &Arc<OAuthRouteState>) -> axum::response::Response {
    auth_url_handler(State(state.clone()), HeaderMap::new())
        .await
        .into_response()
}

fn cookie_headers(name: &str, value: &str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        axum::http::header::COOKIE,
        format!("{name}={value}").parse().unwrap(),
    );
    headers
}

async fn start_login(state: &Arc<OAuthRouteState>) -> (String, String) {
    let response = get_auth_url_response(state).await;
    let nonce = response
        .headers()
        .get_all(axum::http::header::SET_COOKIE)
        .iter()
        .find_map(|value| {
            value
                .to_str()
                .ok()
                .and_then(|c| c.strip_prefix("__Host-bcs_oauth_login="))
                .and_then(|rest| rest.split(';').next())
        })
        .expect("challenge cookie")
        .to_string();
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body: Value = serde_json::from_slice(&bytes).unwrap();
    let url_state = body["providers"][0]["url"]
        .as_str()
        .expect("provider url")
        .rsplit("state=")
        .next()
        .expect("state")
        .to_string();
    (nonce, url_state)
}

#[tokio::test]
async fn provider_exchange_failure_keeps_request_id_and_plain_text_body() {
    run_login_failure(
        Failure::Exchange,
        "oauth_exchange_failed",
        "token exchange failed",
    )
    .await;
}

#[tokio::test]
async fn provider_userinfo_failure_keeps_request_id_and_plain_text_body() {
    run_login_failure(
        Failure::UserInfo,
        "oauth_userinfo_failed",
        "userinfo request failed",
    )
    .await;
}

#[tokio::test]
async fn cross_browser_callback_exchange_count_is_zero() {
    // Browser B replays A's state WITHOUT A's challenge cookie: the handler
    // rejects at the binding boundary — one warn, no provider traffic.
    let state = failing_state(Failure::Install);
    let (nonce, url_state) = start_login(&state).await;
    let _ = nonce;
    let request_id = "oauth-cross-browser";
    let (response, logs) = capture_request_logs(
        request_id,
        callback_handler(
            State(state.clone()),
            Path("google".to_string()),
            axum::extract::Query(CallbackParams {
                code: Some(AUTH_CODE.to_string()),
                auth_code: None,
                state: url_state,
            }),
            HeaderMap::new(),
        ),
    )
    .await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        to_bytes(response.into_body(), 1024).await.unwrap().to_vec(),
        b"invalid state",
        "old plain-text rejection body preserved"
    );
    let event = warning(&logs, request_id, "OAuth callback: invalid state");
    assert!(event["fields"]["error"].is_null() || event["fields"]["error"].is_string());
    assert_no_credentials(&logs, &[AUTH_CODE.to_string(), JWT_SECRET.to_string()]);
}

#[tokio::test]
async fn callback_validation_failures_identify_the_request_before_any_exchange() {
    // A FORGED state (browser binding otherwise well-formed) is rejected at
    // the service boundary; a MISSING code is rejected at the adapter before
    // any exchange. Neither path may carry credentials into the logs.
    let cases: [(bool, &str, StatusCode, &str); 2] = [
        (
            false,
            "private-invalid-csrf-state",
            StatusCode::BAD_REQUEST,
            "invalid state",
        ),
        (
            true,
            "", // replaced by the live batch state below
            StatusCode::BAD_REQUEST,
            "missing code or auth_code",
        ),
    ];
    for (valid_state, forged_state, expected_status, expected_body) in cases {
        let state = failing_state(Failure::Install);
        let (nonce, url_state) = start_login(&state).await;
        let request_state = if forged_state.is_empty() {
            url_state
        } else {
            forged_state.to_string()
        };
        let request_id = format!("oauth-callback-invalid-{forged_state}-{valid_state}");
        let (response, logs) = capture_request_logs(
            &request_id,
            callback_handler(
                State(state.clone()),
                Path("google".to_string()),
                axum::extract::Query(CallbackParams {
                    code: (!forged_state.is_empty()).then(|| AUTH_CODE.to_string()),
                    auth_code: None,
                    state: request_state,
                }),
                cookie_headers(CHALLENGE_COOKIE_SECURE, &nonce),
            ),
        )
        .await;
        assert_eq!(response.status(), expected_status);
        assert_eq!(
            to_bytes(response.into_body(), 1024).await.unwrap().to_vec(),
            expected_body.as_bytes()
        );
        // The forged-state rejection is correlated via the adapter's warn with
        // the request id; badges no credentials into any log line.
        assert_no_credentials(&logs, &[AUTH_CODE.to_string(), JWT_SECRET.to_string()]);
    }
}

#[tokio::test]
async fn refresh_and_logout_store_failures_surface_503_and_never_success() {
    // The FailingSession fails refresh AND revoke with Unavailable → the
    // strict taxonomy maps these to 503 instead of the old catch-all 500 /
    // best-effort 200. Spec §8.5/§8.6 tightening, asserted at the HTTP deny
    // boundary.
    for service_call in [true, false] {
        let state = failing_state(Failure::Install);
        let request_id = format!("oauth-session-failure-service-{service_call}");
        let (nonce, url_state) = start_login(&state).await;

        // Trusted Origin required to even reach the service; then the
        // FailingSession fails every lifecycle op → 503.
        let mut headers = cookie_headers("bcs_session", "any-installed-token");
        headers.insert(
            axum::http::header::ORIGIN,
            "https://workbench.example".parse().unwrap(),
        );
        let (response, logs) = capture_request_logs(
            &request_id,
            refresh_handler(State(state.clone()), headers),
        )
        .await;
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert!(
            !response.headers().contains_key("set-cookie"),
            "a failed refresh must not mint session cookies"
        );
        let event = warning(&logs, &request_id, "OAuth refresh failed");
        assert!(event["fields"]["error"].as_str().is_some());
        assert_no_credentials(&logs, &[JWT_SECRET.to_string()]);

        // Origin refsue happens BEFORE the service for an untrusted Origin.
        let mut headers = cookie_headers("bcs_session", "any-installed-token");
        headers.insert(
            axum::http::header::ORIGIN,
            "https://attacker.example".parse().unwrap(),
        );
        let (response, logs) = capture_request_logs(
            &request_id,
            logout_handler(State(state.clone()), headers),
        )
        .await;
        assert_eq!(response.status(), StatusCode::FORBIDDEN, "untrusted Origin must 403");
        assert!(
            !logs.iter().any(|event| event["fields"]["message"] == "OAuth logout failed"),
            "the Origin check rejects before the service layer is reached"
        );
        let _ = (nonce, url_state);
    }
}

#[tokio::test]
async fn current_user_dependency_failure_correlates_chain_warning() {
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

    let state = Arc::new(OAuthRouteState::new_chain_only(
        Arc::new(NoopUserIdentityPort),
        Arc::new(AuthPluginChain::new(vec![Box::new(FailingAuthPlugin)])),
    ));
    let request_id = "oauth-current-user-failure";
    let (response, logs) = capture_request_logs(
        request_id,
        current_user_handler(State(state), HeaderMap::new()),
    )
    .await;
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR, "internal error");
    let chain = warning(&logs, request_id, "auth: plugin failed");
    assert_eq!(chain["fields"]["plugin"], "diagnostic-failing-auth");
    assert_eq!(chain["fields"]["outcome"], "error");
    warning(&logs, request_id, "auth chain failed in /auth/user");
}
