//! Session flow orchestration: `refresh_session` + `logout` + `current_user`.
//!
//! These flows are simpler than the login flow because they only talk
//! to [`bcs_service_api::port::oauth::OAuthSessionPort`] (refresh /
//! revoke) and the authenticated-caller projection. No
//! [`bcs_service_api::port::oauth::PendingOAuthLoginPort`] or
//! [`bcs_service_api::port::oauth::OAuthProviderPort`] traffic occurs
//! here. Cookie-change sequencing:
//!
//! - `refresh_session` success → `[SetSession { new token, expires_at }]`.
//!   Error → empty cookie changes (the delivery adapter clears the
//!   session cookie as part of its 401 mapping; the application layer
//!   never does so on a refresh error).
//! - `logout(team=None PresentedSession)` → revoke + `[ClearSession]`.
//! - `logout(None PresentedSession)` → no revoke call, idempotent
//!   `Ok(())` + `[ClearSession]` (no cookie / no session is the valid
//!   "already-logged-out" boundary).
//! - `revoke` failure → error reply with empty cookie changes (the
//!   session is still live server-side; a client that wants to logout
//!   anyway can retry against a degraded store).
//! - `current_user` is a pure projection and emits NO cookie changes
//!   (its return type is `Result`, not `AuthFlowReply`).

use bcs_service_api::application::v1::{
    AuthFlowReply, AuthUserInfo, AuthenticatedUserQuery, BrowserCookieChange, PresentedSession,
    SessionRenewal,
};
use bcs_service_api::application::v1::{ApplicationError, require_authenticated_user};

use crate::AuthApplicationService;
use crate::login::current_unix_seconds;

/// Refresh a presented session token via the OAuth session port and
/// reply a `SetSession` cookie change with the newly rotated token.
pub(crate) async fn refresh_session(
    svc: &AuthApplicationService,
    command: PresentedSession,
) -> AuthFlowReply<SessionRenewal> {
    let now = current_unix_seconds();
    match svc.session_port().refresh(&command.token, now).await {
        Ok(session) => AuthFlowReply {
            result: Ok(SessionRenewal {
                // V1 JSON shape preserved — no cookie payload here; the
                // Set-Cookie header is built from `cookie_changes`.
                set_cookie: String::new(),
            }),
            cookie_changes: vec![BrowserCookieChange::SetSession {
                token: session.token,
                expires_at: session.expires_at,
            }],
        },
        Err(error) => AuthFlowReply {
            result: Err(error),
            // The delivery adapter maps the error to its session-clearing
            // 401 response at the HTTP boundary; the application layer
            // emits NO cookie change for a refresh failure. Sessions
            // stay alive only on the success branch.
            cookie_changes: Vec::new(),
        },
    }
}

/// Revoke and clear the session. `Some(token)` calls the session port;
/// `None` is the valid idempotent logout boundary (no session presented,
/// nothing to revoke; the delivery adapter still clears the cookie).
pub(crate) async fn logout(
    svc: &AuthApplicationService,
    command: Option<PresentedSession>,
) -> AuthFlowReply<()> {
    if let Some(presented) = command {
        if let Err(error) = svc.session_port().revoke(&presented.token).await {
            // Revoke failure: the session is still alive server-side;
            // the caller can retry. The user-facing logout flow is
            // incomplete until the store confirms.
            return AuthFlowReply {
                result: Err(error),
                cookie_changes: Vec::new(),
            };
        }
    }
    AuthFlowReply {
        result: Ok(()),
        cookie_changes: vec![BrowserCookieChange::ClearSession],
    }
}

/// Project the already-authenticated Human into the `/auth/user` V1
/// shape. `provider_label` and `avatar` are TRUSTED display data
/// supplied by the delivery adapter; they are NEVER authorization.
pub(crate) async fn current_user(
    _svc: &AuthApplicationService,
    query: AuthenticatedUserQuery,
) -> Result<AuthUserInfo, ApplicationError> {
    let user = require_authenticated_user(&query.caller)?;
    // Display-name preference: `display_name` first, then `full_name`.
    // Both are equally trusted display fields from the upstream
    // identity provider — neither is used for authorization here.
    let name = user.display_name.clone().or_else(|| user.full_name.clone());
    Ok(AuthUserInfo {
        user_id: user.id.clone(),
        name,
        provider: query.provider_label,
        avatar: query.avatar,
    })
}
