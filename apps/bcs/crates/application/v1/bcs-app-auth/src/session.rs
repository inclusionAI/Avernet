//! Session use cases (plan Task 10 step 3): the Human projection, session
//! refresh, and logout. Session credentials arrive already extracted by
//! delivery as a [`PresentedSession`] — this layer never parses a cookie
//! or a Header, and the strict session engine keeps rotation/revocation
//! semantics. Logging carries only stable categories (plan §8.4): no
//! tokens, and no error message text.

use bcs_service_api::application::v1::error::ApplicationError;
use bcs_service_api::application::v1::{
    AuthenticatedUserQuery, AuthFlowReply, AuthUserInfo, BrowserCookieChange, LogoutData,
    PresentedSession, SessionRenewalData,
};

use crate::login::unix_now;
use crate::AuthApplicationService;

/// Project the ALREADY-authenticated Human caller. No credential chain
/// runs here: the caller, provider label, and avatar are trusted-adapter
/// input, and the label/avatar are display data only — never authority.
/// The projection itself is the ONE shared `project_current_user` in the
/// service contract, so a delivery-side projection of a verified caller
/// (no-OAuth deployments) cannot drift from this path.
pub(super) async fn current_user_flow(
    _service: &AuthApplicationService,
    query: AuthenticatedUserQuery,
) -> Result<AuthUserInfo, ApplicationError> {
    match bcs_service_api::application::v1::project_current_user(&query) {
        Ok(user) => Ok(user),
        Err(error) => {
            tracing::debug!("current user rejected: caller has no human identity");
            Err(error)
        }
    }
}

/// Rotate the presented session; the rotated credential arrives as a
/// `SetSession` cookie instruction, per the strict engine's semantics.
pub(super) async fn refresh_session_flow(
    service: &AuthApplicationService,
    session: PresentedSession,
) -> AuthFlowReply<SessionRenewalData> {
    match service.sessions.refresh(&session.token, unix_now()).await {
        Ok(rotated) => {
            tracing::debug!("login session refreshed");
            AuthFlowReply {
                result: Ok(SessionRenewalData {}),
                cookie_changes: vec![BrowserCookieChange::SetSession {
                    token: rotated.token,
                    expires_at: rotated.expires_at,
                }],
            }
        }
        Err(error) => {
            tracing::warn!(category = error.code(), "session refresh rejected");
            AuthFlowReply {
                result: Err(error),
                cookie_changes: Vec::new(),
            }
        }
    }
}

/// Logout: revoke the presented session when there is one; `None` (no
/// cookie) is a valid idempotent exit that never touches the session
/// port. Both revoke outcomes (revoked / not current) are port-level
/// idempotent successes.
pub(super) async fn logout_flow(
    service: &AuthApplicationService,
    session: Option<PresentedSession>,
) -> AuthFlowReply<LogoutData> {
    let Some(session) = session else {
        tracing::debug!("cookie-less logout completed");
        return AuthFlowReply {
            result: Ok(LogoutData {}),
            cookie_changes: vec![BrowserCookieChange::ClearSession],
        };
    };
    match service.sessions.revoke(&session.token).await {
        Ok(()) => {
            tracing::debug!("login session revoked");
            AuthFlowReply {
                result: Ok(LogoutData {}),
                cookie_changes: vec![BrowserCookieChange::ClearSession],
            }
        }
        Err(error) => {
            tracing::warn!(category = error.code(), "session revoke failed");
            AuthFlowReply {
                result: Err(error),
                cookie_changes: Vec::new(),
            }
        }
    }
}
