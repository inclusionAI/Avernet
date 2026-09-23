//! Integration tests for the V1 secure-auth session flows (Task 10):
//! `refresh_session`, `logout`, and `current_user`.

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_auth::{AuthApplicationService, AuthApplicationServiceConfig};
use bcs_service_api::application::v1::{
    ApplicationError, AuthFlowReply, AuthenticatedCaller, AuthenticatedUserIdentity,
    BrowserCookieChange, SessionRenewal,
};
use bcs_service_api::application::v1::auth::{AuthenticatedUserQuery, PresentedSession, AuthService};
use bcs_service_api::port::oauth::{
    BrowserSession, OAuthProviderPort, OAuthSessionPort, PendingOAuthLoginPort,
};

// ───────────────────────────── fakes ──────────────────────────────

#[derive(Default)]
struct FakeProvider;

#[async_trait]
impl OAuthProviderPort for FakeProvider {
    async fn names(&self) -> Vec<String> {
        Vec::new()
    }
    async fn auth_url(
        &self,
        _provider: &str,
        _state: &str,
        _redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        Err(ApplicationError::internal("auth_url not used in session_flow tests"))
    }
    async fn exchange_user(
        &self,
        _provider: &str,
        _code: &str,
        _redirect_uri: &str,
    ) -> Result<bcs_service_api::port::oauth::ExternalLoginIdentity, ApplicationError> {
        Err(ApplicationError::internal("exchange not used in session_flow tests"))
    }
}

struct FakePending;

#[async_trait]
impl PendingOAuthLoginPort for FakePending {
    async fn issue_batch(
        &self,
        _providers: &[String],
        _callback_base: &str,
        _flow: &str,
        _now: u64,
    ) -> Result<bcs_service_api::port::oauth::PendingLoginBatch, ApplicationError> {
        Err(ApplicationError::internal("issue_batch not used in session_flow tests"))
    }
    async fn consume(
        &self,
        _state: &str,
        _browser_nonce: &str,
        _provider: &str,
        _exact_callback: &str,
        _flow: &str,
        _now: u64,
    ) -> Result<(), ApplicationError> {
        Err(ApplicationError::internal("consume not used in session_flow tests"))
    }
}

struct FakeSession {
    refresh_calls: Mutex<Vec<String>>,
    revoke_calls: Mutex<Vec<String>>,
    refresh_outcome: Mutex<Option<Result<BrowserSession, ApplicationError>>>,
    revoke_outcome: Mutex<Option<Result<(), ApplicationError>>>,
}

impl FakeSession {
    fn default_success() -> Self {
        Self {
            refresh_calls: Mutex::new(Vec::new()),
            revoke_calls: Mutex::new(Vec::new()),
            refresh_outcome: Mutex::new(Some(Ok(BrowserSession {
                token: "refreshed-token".to_string(),
                expires_at: 1_700_000_700,
            }))),
            revoke_outcome: Mutex::new(Some(Ok(()))),
        }
    }

    fn with_refresh_fail(err: ApplicationError) -> Self {
        Self {
            refresh_outcome: Mutex::new(Some(Err(err))),
            ..Self::default_success()
        }
    }

    fn refresh_calls_snapshot(&self) -> Vec<String> {
        self.refresh_calls.lock().expect("lock").clone()
    }

    fn revoke_calls_snapshot(&self) -> Vec<String> {
        self.revoke_calls.lock().expect("lock").clone()
    }
}

#[async_trait]
impl OAuthSessionPort for FakeSession {
    async fn install_identity(
        &self,
        _identity: bcs_service_api::port::oauth::ExternalLoginIdentity,
        _now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        Err(ApplicationError::internal("install not used in session_flow tests"))
    }
    async fn refresh(&self, token: &str, _now: u64) -> Result<BrowserSession, ApplicationError> {
        self.refresh_calls.lock().expect("lock").push(token.to_string());
        self.refresh_outcome.lock().expect("lock").take().expect("refresh outcome preset")
    }
    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        self.revoke_calls.lock().expect("lock").push(token.to_string());
        self.revoke_outcome.lock().expect("lock").take().expect("revoke outcome preset")
    }
}

// ──────────────────────────── test stack ───────────────────────────

fn config() -> AuthApplicationServiceConfig {
    AuthApplicationServiceConfig {
        enabled_providers: vec!["github".to_string()],
        flow: "openapi.v1".to_string(),
        post_login_redirect: "https://host/workbench".to_string(),
    }
}

fn service(session: Arc<FakeSession>) -> AuthApplicationService {
    AuthApplicationService::new(
        Arc::new(FakeProvider) as Arc<dyn OAuthProviderPort>,
        session.clone() as Arc<dyn OAuthSessionPort>,
        Arc::new(FakePending) as Arc<dyn PendingOAuthLoginPort>,
        config(),
    )
}

// ──────────────────────────── tests ────────────────────────────────

#[tokio::test]
async fn refresh_session_calls_session_port_and_sets_session_cookie() {
    let session = Arc::new(FakeSession::default_success());
    let svc = service(session.clone());

    let reply: AuthFlowReply<SessionRenewal> = svc
        .refresh_session(PresentedSession {
            token: "raw-token".to_string(),
        })
        .await;

    let renewal = reply.result.expect("refresh success");
    assert_eq!(renewal.set_cookie, "", "old SessionRenewal.set_cookie shape preserved empty");

    assert_eq!(reply.cookie_changes.len(), 1, "exactly SetSession");
    match &reply.cookie_changes[0] {
        BrowserCookieChange::SetSession { token, expires_at } => {
            assert_eq!(token, "refreshed-token");
            assert_eq!(*expires_at, 1_700_000_700);
        }
        other => panic!("expected SetSession, got {other:?}"),
    }

    assert_eq!(session.refresh_calls_snapshot(), vec!["raw-token".to_string()]);
    assert!(
        session.revoke_calls_snapshot().is_empty(),
        "revoke not called during refresh",
    );
}

#[tokio::test]
async fn refresh_session_error_replies_no_cookie_changes() {
    let session = Arc::new(FakeSession::with_refresh_fail(ApplicationError::unavailable(
        "session store unavailable",
    )));
    let svc = service(session.clone());

    let reply: AuthFlowReply<SessionRenewal> = svc
        .refresh_session(PresentedSession {
            token: "raw-token".to_string(),
        })
        .await;

    assert!(reply.result.is_err(), "refresh failure surfaces as error");
    assert!(
        reply.cookie_changes.is_empty(),
        "refresh error carries NO cookie changes — delivery adapter clears session via 401",
    );
    assert_eq!(session.refresh_calls_snapshot().len(), 1, "refresh attempted once");
}

#[tokio::test]
async fn logout_with_token_revokes_and_clears_session() {
    let session = Arc::new(FakeSession::default_success());
    let svc = service(session.clone());

    let reply: AuthFlowReply<()> = svc.logout(Some(PresentedSession { token: "raw-token".to_string() })).await;

    assert!(reply.result.is_ok(), "logout with token succeeds");
    assert_eq!(reply.cookie_changes.len(), 1, "exactly ClearSession");
    match &reply.cookie_changes[0] {
        BrowserCookieChange::ClearSession => {}
        other => panic!("expected ClearSession, got {other:?}"),
    }

    assert_eq!(session.revoke_calls_snapshot(), vec!["raw-token".to_string()]);
}

#[tokio::test]
async fn logout_without_token_is_idempotent_success() {
    let session = Arc::new(FakeSession::default_success());
    let svc = service(session.clone());

    let reply: AuthFlowReply<()> = svc.logout(None).await;

    assert!(reply.result.is_ok(), "logout(None) is idempotent success");
    assert_eq!(reply.cookie_changes.len(), 1, "exactly ClearSession");
    match &reply.cookie_changes[0] {
        BrowserCookieChange::ClearSession => {}
        other => panic!("expected ClearSession, got {other:?}"),
    }

    assert!(
        session.revoke_calls_snapshot().is_empty(),
        "revoke NOT called when no session was presented",
    );
}

#[tokio::test]
async fn current_user_projects_authenticated_human() {
    let session = Arc::new(FakeSession::default_success());
    let svc = service(session.clone());

    let caller = AuthenticatedCaller {
        tenant: Some("acme".to_string()),
        user: Some(AuthenticatedUserIdentity {
            id: "u-1".to_string(),
            username: "alice".to_string(),
            display_name: Some("Alice".to_string()),
            full_name: Some("Alice Liu".to_string()),
        }),
        bot: None,
        app: None,
        access_key: None,
    };

    let info = svc
        .current_user(AuthenticatedUserQuery {
            caller,
            provider_label: "github".to_string(),
            avatar: Some("https://avatar/u-1.png".to_string()),
        })
        .await
        .expect("current_user success");

    // user_id mirrors the authenticated subject id.
    assert_eq!(info.user_id, "u-1");
    // name preferred from display_name (with full_name fallback); both
    // are equally trusted display data from the upstream identity.
    assert_eq!(info.name.as_deref(), Some("Alice"));
    // provider/avatar are trusted-display ONLY, never authorization.
    assert_eq!(info.provider, "github", "provider = adapter-supplied label");
    assert_eq!(info.avatar.as_deref(), Some("https://avatar/u-1.png"));
}

#[tokio::test]
async fn current_user_falls_back_to_full_name_when_display_name_absent() {
    let session = Arc::new(FakeSession::default_success());
    let svc = service(session.clone());

    let caller = AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            id: "u-2".to_string(),
            username: "blake".to_string(),
            display_name: None,
            full_name: Some("Blake Smith".to_string()),
        }),
        bot: None,
        app: None,
        access_key: None,
    };

    let info = svc
        .current_user(AuthenticatedUserQuery {
            caller,
            provider_label: "google".to_string(),
            avatar: None,
        })
        .await
        .expect("current_user success");

    assert_eq!(info.user_id, "u-2");
    assert_eq!(info.name.as_deref(), Some("Blake Smith"), "full_name fallback when display_name is None");
    assert_eq!(info.provider, "google");
    assert!(info.avatar.is_none(), "avatar pass-through None");
}

#[tokio::test]
async fn current_user_rejects_caller_without_user_identity() {
    let session = Arc::new(FakeSession::default_success());
    let svc = service(session.clone());

    let caller = AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: None,
        app: None,
        access_key: None,
    };

    let result = svc
        .current_user(AuthenticatedUserQuery {
            caller,
            provider_label: "github".to_string(),
            avatar: None,
        })
        .await;

    assert!(result.is_err(), "current_user fails when caller has no human identity");
    let err = result.expect_err("error expected");
    assert!(
        matches!(err, ApplicationError::Forbidden(_)),
        "missing human identity surfaces as Forbidden: {err:?}",
    );
}
