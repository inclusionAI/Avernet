//! Integration tests for the V1 secure-auth login flows (Task 10).
//!
//! Fakes for the three ports (PendingOAuthLoginPort, OAuthProviderPort,
//! OAuthSessionPort) record call args so the tests can assert exact call
//! counts, URL order, cookie-change sets per failure mode, and the
//! redirect destination. The application service is the only real
//! implementation under test.

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_auth::{AuthApplicationService, AuthApplicationServiceConfig};
use bcs_service_api::application::v1::{
    ApplicationError, AuthFlowReply, AuthProviderUrl, AuthRedirect, BrowserLoginBinding,
    BuildLoginUrls, BrowserCookieChange, CompleteOAuthLogin,
};
use bcs_service_api::application::v1::auth::AuthService;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort,
    PendingLoginBatch, PendingOAuthLoginPort,
};

// ───────────────────────────── fakes ──────────────────────────────

#[derive(Default)]
struct FakePending {
    issue_calls: Mutex<Vec<(Vec<String>, String, String)>>,
    consume_calls: Mutex<Vec<(String, String, String, String, String)>>,
    /// `(state, nonce, provider, exact_callback, flow)` arg counter;
    /// the impl takes one entry per outcome so we can replay multiple
    /// outcomes in order. `Vec` so test controls pop behaviour.
    consume_outcomes: Mutex<Vec<Result<(), ApplicationError>>>,
    /// Whether to fail `issue_batch` with this error (None = success).
    batch_disabled: Mutex<Option<ApplicationError>>,
    /// Nonce returned by `issue_batch`.
    issue_nonce: String,
    /// Expiry returned by `issue_batch`.
    issue_expires_at: u64,
    /// Per-provider `(provider, state)` pairs the fake builds on
    /// `issue_batch`. Each provider maps to one (provider, state). To
    /// let findings scale, the fakes derive `state` from the provider
    /// name: `format!("state-{provider}")`.
    issue_states: Mutex<Vec<(String, String)>>,
}

impl FakePending {
    fn default_success() -> Self {
        Self {
            issue_calls: Mutex::new(Vec::new()),
            consume_calls: Mutex::new(Vec::new()),
            consume_outcomes: Mutex::new(vec![Ok(())]),
            batch_disabled: Mutex::new(None),
            issue_nonce: "issue-nonce".to_string(),
            issue_expires_at: 1_700_000_300,
            issue_states: Mutex::new(Vec::new()),
        }
    }

    fn with_consume_outcomes(outcomes: Vec<Result<(), ApplicationError>>) -> Self {
        Self {
            consume_outcomes: Mutex::new(outcomes),
            issue_nonce: "issue-nonce".to_string(),
            issue_expires_at: 1_700_000_300,
            ..Self::default_success()
        }
    }

    fn with_batch_disabled(err: ApplicationError) -> Self {
        Self {
            batch_disabled: Mutex::new(Some(err)),
            ..Self::default_success()
        }
    }

    fn consume_calls_snapshot(&self) -> Vec<(String, String, String, String, String)> {
        self.consume_calls.lock().expect("lock").clone()
    }

    fn issue_calls_snapshot(&self) -> Vec<(Vec<String>, String, String)> {
        self.issue_calls.lock().expect("lock").clone()
    }
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
        self.issue_calls.lock().expect("lock").push((
            providers.to_vec(),
            callback_base.to_string(),
            flow.to_string(),
        ));
        if let Some(err) = self.batch_disabled.lock().expect("lock").take() {
            return Err(err);
        }
        let states: Vec<(String, String)> = providers
            .iter()
            .map(|p| (p.clone(), format!("state-{p}")))
            .collect();
        *self.issue_states.lock().expect("lock") = states.clone();
        Ok(PendingLoginBatch {
            browser_nonce: self.issue_nonce.clone(),
            expires_at: self.issue_expires_at,
            provider_states: states,
        })
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
        self.consume_calls.lock().expect("lock").push((
            state.to_string(),
            browser_nonce.to_string(),
            provider.to_string(),
            exact_callback.to_string(),
            flow.to_string(),
        ));
        // Drain exactly one entry from the outcome queue so each consume
        // call consumes exactly one preset outcome (and the MutexGuard
        // is released as soon as `drain` + `next` resolve, before the
        // match runs). Avoids the re-entrant-lock deadlock the previous
        // `first()` + `remove(0)` approach hit when ApplicationError is
        // not Clone.
        let outcome: Option<Result<(), ApplicationError>> = self
            .consume_outcomes
            .lock()
            .expect("lock")
            .drain(0..1)
            .next();
        match outcome {
            Some(r) => r,
            None => Ok(()),
        }
    }
}

struct FakeProvider {
    exchange_calls: Mutex<Vec<(String, String, String)>>,
    /// Single preset exchange outcome; successive calls reuse the same
    /// response. `ApplicationError` is not Clone, so call-sites produce
    /// fresh `Ok(identity)` results on demand by storing the factory
    /// outputs as preset `Identity` + never-fail `exchange_outcome`.
    exchange_fail: Mutex<Option<ApplicationError>>,
    exchange_identity: ExternalLoginIdentity,
    auth_url_calls: Mutex<Vec<(String, String, String)>>,
}

impl FakeProvider {
    fn default_success() -> Self {
        Self {
            exchange_calls: Mutex::new(Vec::new()),
            exchange_fail: Mutex::new(None),
            exchange_identity: ExternalLoginIdentity {
                provider: "github".to_string(),
                external_user_id: "ext-uid".to_string(),
                name: Some("mock user".to_string()),
                avatar: Some("https://avatar/mock".to_string()),
            },
            auth_url_calls: Mutex::new(Vec::new()),
        }
    }

    fn with_exchange_fail(err: ApplicationError) -> Self {
        Self {
            exchange_fail: Mutex::new(Some(err)),
            ..Self::default_success()
        }
    }

    fn exchange_calls_snapshot(&self) -> Vec<(String, String, String)> {
        self.exchange_calls.lock().expect("lock").clone()
    }

    fn auth_url_calls_snapshot(&self) -> Vec<(String, String, String)> {
        self.auth_url_calls.lock().expect("lock").clone()
    }
}

#[async_trait]
impl OAuthProviderPort for FakeProvider {
    async fn names(&self) -> Vec<String> {
        vec!["github".to_string(), "google".to_string()]
    }

    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        self.auth_url_calls.lock().expect("lock").push((
            provider.to_string(),
            state.to_string(),
            redirect_uri.to_string(),
        ));
        Ok(format!("https://provider/{provider}?state={state}&redirect_uri={redirect_uri}"))
    }

    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        self.exchange_calls.lock().expect("lock").push((
            provider.to_string(),
            code.to_string(),
            redirect_uri.to_string(),
        ));
        if let Some(err) = self.exchange_fail.lock().expect("lock").take() {
            return Err(err);
        }
        // Each call returns a fresh Identity derived from the preset so
        // `ApplicationError` is non-Clone-friendly.
        Ok(ExternalLoginIdentity {
            provider: provider.to_string(),
            external_user_id: format!("ext-{provider}-{code}"),
            name: self.exchange_identity.name.clone(),
            avatar: self.exchange_identity.avatar.clone(),
        })
    }
}

#[derive(Default)]
struct FakeSession {
    install_calls: Mutex<Vec<ExternalLoginIdentity>>,
    refresh_calls: Mutex<Vec<String>>,
    revoke_calls: Mutex<Vec<String>>,
    install_outcome: Mutex<Option<Result<BrowserSession, ApplicationError>>>,
    refresh_outcome: Mutex<Option<Result<BrowserSession, ApplicationError>>>,
    revoke_outcome: Mutex<Option<Result<(), ApplicationError>>>,
}

impl FakeSession {
    fn default_success() -> Self {
        Self {
            install_calls: Mutex::new(Vec::new()),
            refresh_calls: Mutex::new(Vec::new()),
            revoke_calls: Mutex::new(Vec::new()),
            install_outcome: Mutex::new(Some(Ok(BrowserSession {
                token: "install-token".to_string(),
                expires_at: 1_700_000_500,
            }))),
            refresh_outcome: Mutex::new(Some(Ok(BrowserSession {
                token: "refresh-token".to_string(),
                expires_at: 1_700_000_700,
            }))),
            revoke_outcome: Mutex::new(Some(Ok(()))),
        }
    }

    fn with_install_fail(err: ApplicationError) -> Self {
        Self {
            install_outcome: Mutex::new(Some(Err(err))),
            ..Self::default_success()
        }
    }

    fn install_calls_snapshot(&self) -> Vec<ExternalLoginIdentity> {
        self.install_calls.lock().expect("lock").clone()
    }
}

#[async_trait]
impl OAuthSessionPort for FakeSession {
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        _now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        self.install_calls.lock().expect("lock").push(identity);
        self.install_outcome
            .lock()
            .expect("lock")
            .take()
            .expect("install outcome preset")
    }

    async fn refresh(&self, token: &str, _now: u64) -> Result<BrowserSession, ApplicationError> {
        self.refresh_calls.lock().expect("lock").push(token.to_string());
        self.refresh_outcome
            .lock()
            .expect("lock")
            .take()
            .expect("refresh outcome preset")
    }

    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        self.revoke_calls.lock().expect("lock").push(token.to_string());
        self.revoke_outcome
            .lock()
            .expect("lock")
            .take()
            .expect("revoke outcome preset")
    }
}

// ──────────────────────────── test stack ───────────────────────────

fn config() -> AuthApplicationServiceConfig {
    AuthApplicationServiceConfig {
        enabled_providers: vec!["github".to_string(), "google".to_string()],
        flow: "openapi.v1".to_string(),
        post_login_redirect: "https://host/workbench".to_string(),
    }
}

fn service(
    pending: Arc<FakePending>,
    provider: Arc<FakeProvider>,
    session: Arc<FakeSession>,
) -> AuthApplicationService {
    AuthApplicationService::new(
        provider.clone() as Arc<dyn OAuthProviderPort>,
        session.clone() as Arc<dyn OAuthSessionPort>,
        pending.clone() as Arc<dyn PendingOAuthLoginPort>,
        config(),
    )
}

// ──────────────────────────── tests ────────────────────────────────

#[tokio::test]
async fn login_urls_preserves_chain_subsequence_order() {
    let pending = Arc::new(FakePending::default_success());
    let provider = Arc::new(FakeProvider::default_success());
    let session = Arc::new(FakeSession::default_success());
    let svc = service(pending.clone(), provider.clone(), session.clone());

    let reply: AuthFlowReply<Vec<AuthProviderUrl>> = svc
        .login_urls(BuildLoginUrls {
            callback_base_url: "https://host/openapi/v1/auth/callback".to_string(),
        })
        .await;

    let urls = reply.result.expect("login_urls success");
    assert_eq!(urls.len(), 2, "preserve configured subsequence length");
    assert_eq!(urls[0].name, "github", "preserve subsequence order");
    assert_eq!(urls[1].name, "google", "preserve subsequence order");
    // The exact callback per provider is `{base}/{provider}`.
    assert!(
        urls[0].url.contains("redirect_uri=https://host/openapi/v1/auth/callback/github"),
        "github URL embeds the per-provider exact callback: {}",
        urls[0].url,
    );
    assert!(
        urls[1].url.contains("redirect_uri=https://host/openapi/v1/auth/callback/google"),
        "google URL embeds the per-provider exact callback: {}",
        urls[1].url,
    );

    // Cookie change set is exactly [SetLoginChallenge] with the issued
    // nonce / expiry.
    assert_eq!(reply.cookie_changes.len(), 1, "exactly SetLoginChallenge");
    match &reply.cookie_changes[0] {
        BrowserCookieChange::SetLoginChallenge { nonce, expires_at } => {
            assert_eq!(nonce, "issue-nonce", "SetLoginChallenge carries issue-batch nonce");
            assert_eq!(*expires_at, 1_700_000_300, "expires_at mirrors issue-batch");
        }
        other => panic!("expected SetLoginChallenge, got {other:?}"),
    }

    // The pending store recorded ONE issue_batch call with the
    // configured flow namespace.
    let issue_calls = pending.issue_calls_snapshot();
    assert_eq!(issue_calls.len(), 1, "issue_batch called once");
    assert_eq!(issue_calls[0].2, "openapi.v1", "configured flow passed through");
    // No exchange/install happened during login_url building.
    assert!(
        provider.exchange_calls_snapshot().is_empty(),
        "exchange_user not called during login_urls",
    );
    assert!(session.install_calls_snapshot().is_empty(), "install not called during login_urls");
}

#[tokio::test]
async fn login_urls_store_failure_replies_error_with_no_cookie_changes() {
    let pending = Arc::new(FakePending::with_batch_disabled(
        ApplicationError::unavailable("pending login store down"),
    ));
    let provider = Arc::new(FakeProvider::default_success());
    let session = Arc::new(FakeSession::default_success());
    let svc = service(pending.clone(), provider.clone(), session.clone());

    let reply: AuthFlowReply<Vec<AuthProviderUrl>> = svc
        .login_urls(BuildLoginUrls {
            callback_base_url: "https://host/openapi/v1/auth/callback".to_string(),
        })
        .await;

    assert!(reply.result.is_err(), "store failure surfaces as error reply");
    assert!(
        reply.cookie_changes.is_empty(),
        "store failure = no cookie changes (challenge NEVER set, nothing to clear)",
    );
    // Provider URL building never reached.
    assert!(
        provider.auth_url_calls_snapshot().is_empty(),
        "auth_url not called after a failed issue_batch",
    );
}

#[tokio::test]
async fn complete_login_wrong_browser_binding_consumes_nothing() {
    let pending = Arc::new(FakePending::with_consume_outcomes(vec![Err(
        ApplicationError::invalid(
            "login_state_invalid",
            "browser binding mismatch (spec §8.4 cross-browser defense)",
        ),
    )]));
    let provider = Arc::new(FakeProvider::default_success());
    let session = Arc::new(FakeSession::default_success());
    let svc = service(pending.clone(), provider.clone(), session.clone());

    let reply: AuthFlowReply<AuthRedirect> = svc
        .complete_login(CompleteOAuthLogin {
            provider: "github".to_string(),
            code: "auth-code-1".to_string(),
            auth_code: None,
            state: "state-github".to_string(),
            callback_base_url: "https://host/openapi/v1/auth/callback".to_string(),
            browser_binding: BrowserLoginBinding {
                nonce: "WRONG-NONCE".to_string(),
            },
        })
        .await;

    assert!(reply.result.is_err(), "wrong-browser binding surfaces an error");
    assert!(
        reply.cookie_changes.is_empty(),
        "binding failure burned nothing — NO ClearLoginChallenge",
    );

    // consume called ONCE with the wrong nonce; subsequent ports untouched.
    let consume_calls = pending.consume_calls_snapshot();
    assert_eq!(consume_calls.len(), 1, "consume attempted exactly once");
    assert_eq!(consume_calls[0].1, "WRONG-NONCE", "wrong nonce passed through");
    assert_eq!(
        consume_calls[0].3,
        "https://host/openapi/v1/auth/callback/github",
        "exact_callback = base/<provider>",
    );
    assert_eq!(consume_calls[0].4, "openapi.v1", "configured flow passed through");

    assert!(
        provider.exchange_calls_snapshot().is_empty(),
        "exchange_user called 0 times after binding failure",
    );
    assert!(
        session.install_calls_snapshot().is_empty(),
        "install called 0 times after binding failure",
    );
}

#[tokio::test]
async fn complete_login_consume_ok_exchange_fail_clears_challenge() {
    let pending = Arc::new(FakePending::default_success());
    let provider = Arc::new(FakeProvider::with_exchange_fail(
        ApplicationError::bad_gateway(
            "oauth_exchange_failed",
            "upstream provider token endpoint unreachable",
        ),
    ));
    let session = Arc::new(FakeSession::default_success());
    let svc = service(pending.clone(), provider.clone(), session.clone());

    let reply: AuthFlowReply<AuthRedirect> = svc
        .complete_login(CompleteOAuthLogin {
            provider: "github".to_string(),
            code: "auth-code-2".to_string(),
            auth_code: None,
            state: "state-github".to_string(),
            callback_base_url: "https://host/openapi/v1/auth/callback".to_string(),
            browser_binding: BrowserLoginBinding {
                nonce: "issue-nonce".to_string(),
            },
        })
        .await;

    assert!(reply.result.is_err(), "exchange failure surfaces as error");
    // Spec §8.4 — the matched callback's challenge is ALWAYS cleared,
    // even when a subsequent step (exchange) fails.
    assert_eq!(
        reply.cookie_changes.len(),
        1,
        "exactly one cookie change after exchange failure",
    );
    match &reply.cookie_changes[0] {
        BrowserCookieChange::ClearLoginChallenge => {}
        other => panic!("expected ClearLoginChallenge, got {other:?}"),
    }

    // consume called ONCE with the right nonce/exact_callback; exchange
    // called ONCE; install NEVER reached.
    let consume_calls = pending.consume_calls_snapshot();
    assert_eq!(consume_calls.len(), 1, "consume succeeded exactly once");
    assert_eq!(consume_calls[0].1, "issue-nonce", "binding nonce carried through");
    let exchange_calls = provider.exchange_calls_snapshot();
    assert_eq!(exchange_calls.len(), 1, "exchange called once");
    assert_eq!(exchange_calls[0].0, "github", "exchange received the right provider");
    assert_eq!(exchange_calls[0].1, "auth-code-2", "code carried through");
    assert!(
        session.install_calls_snapshot().is_empty(),
        "install NOT called when exchange failed",
    );
}

#[tokio::test]
async fn complete_login_consume_ok_install_fail_clears_challenge() {
    let pending = Arc::new(FakePending::default_success());
    let provider = Arc::new(FakeProvider::default_success());
    let session = Arc::new(FakeSession::with_install_fail(ApplicationError::unavailable(
        "session store unavailable",
    )));
    let svc = service(pending.clone(), provider.clone(), session.clone());

    let reply: AuthFlowReply<AuthRedirect> = svc
        .complete_login(CompleteOAuthLogin {
            provider: "github".to_string(),
            code: "auth-code-3".to_string(),
            auth_code: None,
            state: "state-github".to_string(),
            callback_base_url: "https://host/openapi/v1/auth/callback".to_string(),
            browser_binding: BrowserLoginBinding {
                nonce: "issue-nonce".to_string(),
            },
        })
        .await;

    assert!(reply.result.is_err(), "install failure surfaces as error");
    assert_eq!(reply.cookie_changes.len(), 1, "ClearLoginChallenge only");
    match &reply.cookie_changes[0] {
        BrowserCookieChange::ClearLoginChallenge => {}
        other => panic!("expected ClearLoginChallenge, got {other:?}"),
    }

    assert_eq!(provider.exchange_calls_snapshot().len(), 1, "exchange called once");
    assert_eq!(session.install_calls_snapshot().len(), 1, "install attempted once");
}

#[tokio::test]
async fn complete_login_success_sets_session_and_clears_challenge() {
    let pending = Arc::new(FakePending::default_success());
    let provider = Arc::new(FakeProvider::default_success());
    let session = Arc::new(FakeSession::default_success());
    let svc = service(pending.clone(), provider.clone(), session.clone());

    let reply: AuthFlowReply<AuthRedirect> = svc
        .complete_login(CompleteOAuthLogin {
            provider: "github".to_string(),
            code: "auth-code-4".to_string(),
            auth_code: Some("optional-auth-code-extra".to_string()),
            state: "state-github".to_string(),
            callback_base_url: "https://host/openapi/v1/auth/callback".to_string(),
            browser_binding: BrowserLoginBinding {
                nonce: "issue-nonce".to_string(),
            },
        })
        .await;

    let redirect = reply.result.expect("login success");
    assert_eq!(
        redirect.location, "https://host/workbench",
        "redirect location = configured post_login_redirect",
    );
    // old auth shape preserved: set_cookie field exists but is empty
    // (cookie mutations live in cookie_changes).
    assert_eq!(redirect.set_cookie, "", "old set_cookie field empty");

    assert_eq!(
        reply.cookie_changes.len(),
        2,
        "SetSession + ClearLoginChallenge on success",
    );
    match &reply.cookie_changes[0] {
        BrowserCookieChange::SetSession { token, expires_at } => {
            assert_eq!(token, "install-token");
            assert_eq!(*expires_at, 1_700_000_500);
        }
        other => panic!("expected SetSession first, got {other:?}"),
    }
    match &reply.cookie_changes[1] {
        BrowserCookieChange::ClearLoginChallenge => {}
        other => panic!("expected ClearLoginChallenge second, got {other:?}"),
    }

    assert_eq!(provider.exchange_calls_snapshot().len(), 1, "exchange called once");
    assert_eq!(session.install_calls_snapshot().len(), 1, "install called once");
    // The installed identity was the EXTERNAL identity (provider + external_user_id).
    let installed = &session.install_calls_snapshot()[0];
    assert_eq!(installed.provider, "github");
    assert_eq!(installed.external_user_id, "ext-github-auth-code-4");
}

#[tokio::test]
async fn complete_login_rejects_unenabled_provider_with_no_cookie_changes() {
    let pending = Arc::new(FakePending::default_success());
    let provider = Arc::new(FakeProvider::default_success());
    let session = Arc::new(FakeSession::default_success());
    let svc = service(pending.clone(), provider.clone(), session.clone());

    let reply: AuthFlowReply<AuthRedirect> = svc
        .complete_login(CompleteOAuthLogin {
            provider: "untrusted-provider".to_string(),
            code: "any".to_string(),
            auth_code: None,
            state: "state-github".to_string(),
            callback_base_url: "https://host/openapi/v1/auth/callback".to_string(),
            browser_binding: BrowserLoginBinding {
                nonce: "issue-nonce".to_string(),
            },
        })
        .await;

    assert!(reply.result.is_err(), "unknown provider rejected");
    assert!(
        reply.cookie_changes.is_empty(),
        "unknown provider burns nothing — no consume, no cookie changes",
    );
    assert!(
        pending.consume_calls_snapshot().is_empty(),
        "consume NOT called when provider is unenabled",
    );
    assert!(
        provider.exchange_calls_snapshot().is_empty(),
        "exchange NOT called when provider is unenabled",
    );
}
