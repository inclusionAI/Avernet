//! Session use-case contract tests (plan Task 10 step 3): the
//! already-authenticated Human projection, required-token refresh, and
//! idempotent logout — none of which ever parse a cookie or a Header.

mod support;

use std::sync::Arc;

use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity, BrowserCookieChange, PresentedSession,
    AuthService,
};

use support::fake_ports::{FakePendingLoginPort, FakeProviderPort, FakeSessionPort};
use support::auth_service;

fn human_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".to_string()),
        user: Some(AuthenticatedUserIdentity {
            id: "user-9".to_string(),
            username: "octocat".to_string(),
            display_name: Some("Octo Cat".to_string()),
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn wired() -> (bcs_app_auth::AuthApplicationService, Arc<FakeSessionPort>) {
    let providers = Arc::new(FakeProviderPort::new(&["github"]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = auth_service(providers, sessions.clone(), pending);
    (service, sessions)
}

#[tokio::test]
async fn current_user_projects_the_authenticated_human_only() {
    let (service, _sessions) = wired();

    let user = service
        .current_user(bcs_service_api::application::v1::AuthenticatedUserQuery {
            caller: human_caller(),
            provider_label: "github".to_string(),
            avatar: Some("https://cdn.test/avatar.png".to_string()),
        })
        .await
        .expect("Human callers project");

    assert_eq!(user.user_id, "user-9");
    assert_eq!(user.name.as_deref(), Some("Octo Cat"));
    assert_eq!(user.provider, "github");
    assert_eq!(
        user.avatar.as_deref(),
        Some("https://cdn.test/avatar.png"),
        "provider_label/avatar are display data carried through verbatim"
    );
}

#[tokio::test]
async fn current_user_rejects_non_human_callers() {
    let (service, _sessions) = wired();

    let result = service
        .current_user(bcs_service_api::application::v1::AuthenticatedUserQuery {
            caller: AuthenticatedCaller {
                tenant: None,
                user: None,
                bot: None,
                app: None,
                access_key: None,
            },
            provider_label: "github".to_string(),
            avatar: None,
        })
        .await;

    assert!(
        matches!(result, Err(bcs_service_api::application::v1::ApplicationError::Unauthenticated)),
        "the projection view is Human-only; no second chain runs"
    );
}

#[tokio::test]
async fn refresh_rotates_the_session_cookie_from_the_required_token() {
    let (service, sessions) = wired();

    let reply = service
        .refresh_session(PresentedSession {
            token: "current-session-token".to_string(),
        })
        .await;

    let _renewed = reply.result.expect("refresh succeeds");
    assert_eq!(
        reply.cookie_changes,
        vec![BrowserCookieChange::SetSession {
            token: "rotated-session-token".to_string(),
            expires_at: 4_100_000_500,
        }]
    );
    assert_eq!(sessions.refresh_calls().len(), 1);
    assert_eq!(sessions.refresh_calls()[0].token, "current-session-token");
}

#[tokio::test]
async fn refresh_failure_changes_no_cookies() {
    let (service, sessions) = wired();
    sessions.set_refresh_error(bcs_service_api::application::v1::ApplicationError::Unauthenticated);

    let reply = service
        .refresh_session(PresentedSession {
            token: "stale-session-token".to_string(),
        })
        .await;

    assert!(reply.result.is_err());
    assert!(reply.cookie_changes.is_empty());
}

#[tokio::test]
async fn logout_with_a_presented_session_revokes_and_clears() {
    let (service, sessions) = wired();

    let reply = service
        .logout(Some(PresentedSession {
            token: "current-session-token".to_string(),
        }))
        .await;

    assert!(reply.result.is_ok());
    assert_eq!(reply.cookie_changes, vec![BrowserCookieChange::ClearSession]);
    assert_eq!(sessions.revoke_calls(), vec!["current-session-token".to_string()]);
}

#[tokio::test]
async fn logout_without_a_session_is_an_idempotent_success_boundary() {
    let (service, sessions) = wired();

    let reply = service.logout(None).await;

    assert!(reply.result.is_ok(), "no cookie is a valid idempotent exit");
    assert_eq!(
        reply.cookie_changes,
        vec![BrowserCookieChange::ClearSession],
        "the local session cookie is still cleared"
    );
    assert_eq!(sessions.revoke_count(), 0, "nothing to revoke");
}

#[tokio::test]
async fn logout_revoke_failure_propagates_without_cookie_changes() {
    let (service, sessions) = wired();
    sessions.set_revoke_error(bcs_service_api::application::v1::ApplicationError::unavailable(
        "auth_dependency_unavailable",
        "session identity store unavailable",
    ));

    let reply = service
        .logout(Some(PresentedSession {
            token: "current-session-token".to_string(),
        }))
        .await;

    assert!(reply.result.is_err());
    assert!(reply.cookie_changes.is_empty());
}
