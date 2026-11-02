use std::fmt;

use async_trait::async_trait;
use serde::Serialize;

use super::identity::AuthenticatedCaller;
use super::ApplicationError;

/// REQUIRED browser binding of every OAuth callback (plan §8.4): the
/// nonce the temporary login-challenge cookie delivered to the browser
/// that initiated the login. A nonce in a query parameter can never
/// substitute for this binding.
#[derive(Clone)]
pub struct BrowserLoginBinding {
    pub nonce: String,
}

/// Verified OAuth callback command (plan §8.4) carrying the initiating
/// browser's REQUIRED binding. The FINAL `CompleteOAuthLogin` shape:
/// there is no `Option` escape hatch and no second login command type —
/// every entry (legacy and V1) constructs a real [`BrowserLoginBinding`].
#[derive(Clone)]
pub struct CompleteOAuthLogin {
    pub provider: String,
    pub code: Option<String>,
    pub auth_code: Option<String>,
    pub state: String,
    pub callback_base_url: String,
    pub browser_binding: BrowserLoginBinding,
}

/// Transport-independent cookie instructions for the login flow
/// (plan §8.4). Success sets the session WHILE clearing the temporary
/// challenge; failure still clears the challenge. The HTTP adapter owns
/// actual `Set-Cookie` encoding (including the production
/// `__Host-bcs_oauth_login` challenge name); these variants never embed
/// raw `HeaderValue`s.
///
/// `PartialEq` is for exact instruction-sequence assertions only
/// (`Debug` stays redacted).
#[derive(Clone, PartialEq, Eq)]
pub enum BrowserCookieChange {
    /// Write the fresh browser nonce challenge (host-only, HttpOnly,
    /// Secure, SameSite=Lax, Path=/, Max-Age=300 in production).
    SetLoginChallenge {
        nonce: String,
        expires_at: u64,
    },
    /// Remove the temporary login-challenge cookie.
    ClearLoginChallenge,
    /// Install the login session cookie.
    SetSession {
        token: String,
        expires_at: u64,
    },
    /// Remove the login session cookie.
    ClearSession,
}

/// Envelope for every V1 auth flow step: the outcome plus the cookie
/// instructions that MUST be applied on BOTH success and failure paths,
/// so a rejected callback still clears the temporary challenge
/// (plan §8.4). The payload `T` never travels with HTTP response types.
pub struct AuthFlowReply<T> {
    pub result: Result<T, ApplicationError>,
    pub cookie_changes: Vec<BrowserCookieChange>,
}

// Plan §8.4 credential-log red line: the binding nonce, one-time state,
// authorization code, and session token must never reach Debug output —
// not through these types directly, and not through the AuthFlowReply
// envelope. Only type/variant names, the success/failure shape, and
// elision markers are printable.
impl fmt::Debug for BrowserLoginBinding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("BrowserLoginBinding").finish_non_exhaustive()
    }
}

impl fmt::Debug for CompleteOAuthLogin {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CompleteOAuthLogin").finish_non_exhaustive()
    }
}

impl fmt::Debug for BrowserCookieChange {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::SetLoginChallenge { .. } => {
                f.write_str("BrowserCookieChange::SetLoginChallenge { .. }")
            }
            Self::ClearLoginChallenge => f.write_str("BrowserCookieChange::ClearLoginChallenge"),
            Self::SetSession { .. } => f.write_str("BrowserCookieChange::SetSession { .. }"),
            Self::ClearSession => f.write_str("BrowserCookieChange::ClearSession"),
        }
    }
}

impl<T> fmt::Debug for AuthFlowReply<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // The payload may be a login URL list (which embeds state) and
        // the error must stay category-only — print neither's contents.
        let result = if self.result.is_ok() { "ok(..)" } else { "err(..)" };
        f.debug_struct("AuthFlowReply")
            .field("result", &result)
            .field("cookie_changes", &self.cookie_changes.len())
            .finish()
    }
}

/// Command for building OAuth login URLs for a versioned public auth surface.
///
/// `callback_base_url` is the public base callback path without the provider
/// segment, e.g. `https://host/openapi/v1/auth/callback`.
#[derive(Debug, Clone)]
pub struct BuildLoginUrls {
    pub callback_base_url: String,
}

#[derive(Clone, Serialize)]
pub struct AuthProviderUrl {
    pub name: String,
    pub url: String,
}

#[derive(Clone, Serialize)]
pub struct AuthProviderUrlList {
    pub providers: Vec<AuthProviderUrl>,
}

// Plan §8.4 credential-log red line: the authorize URL embeds the issued
// one-time state, so Debug prints only the type shape — never the URL.
impl fmt::Debug for AuthProviderUrl {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("AuthProviderUrl { .. }")
    }
}

impl fmt::Debug for AuthProviderUrlList {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuthProviderUrlList")
            .field("providers", &self.providers.len())
            .finish()
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct AuthUserInfo {
    pub user_id: String,
    pub name: Option<String>,
    pub provider: String,
    pub avatar: Option<String>,
}

/// REQUIRED presentation of an existing login-session credential (V1 API
/// auth plan §8.5). Delivery extracts the cookie and hands the raw token
/// over inside this struct; the application layer never parses a
/// `Cookie` header itself. Debug prints only the type name and elision
/// markers — the bearer token is never formattable (plan §8.4).
#[derive(Clone)]
pub struct PresentedSession {
    pub token: String,
}

impl fmt::Debug for PresentedSession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PresentedSession").finish_non_exhaustive()
    }
}

/// Human projection query on an ALREADY-authenticated caller (V1 API
/// auth plan): the delivery layer's verified credential chain produced
/// `caller`; `provider_label` and `avatar` are trusted-adapter DISPLAY
/// data carried verbatim for the response body — they are never treated
/// as authority. Only Human callers are projectable; there is no second
/// chain execution behind this query.
#[derive(Debug, Clone)]
pub struct AuthenticatedUserQuery {
    pub caller: AuthenticatedCaller,
    pub provider_label: String,
    pub avatar: Option<String>,
}

/// Successful-callback payload: only the configured same-origin redirect
/// location. The session install and challenge cleanup travel exclusively
/// through the reply's `cookie_changes` (plan §8.4).
#[derive(Debug, Clone, Serialize)]
pub struct AuthRedirectLocation {
    pub location: String,
}

/// Refresh payload: the rotated session credential lives only in the
/// reply's cookie instructions, never in a JSON body field.
#[derive(Debug, Clone, Serialize)]
pub struct SessionRenewalData {}

/// Logout payload: logout carries no body data at all.
#[derive(Debug, Clone, Serialize)]
pub struct LogoutData {}

/// Transport-neutral secure OAuth orchestration contract (V1 API auth
/// plan, Tasks 10–11): the ONE entry surface both the legacy `/auth/*`
/// HTTP entry and the V1 `/openapi/v1/auth/*` entry route through. Every
/// implementation injects its ports and configuration; delivery adapters
/// only parse temporary cookies, the request method/Origin, and session
/// cookies, then consume [`AuthFlowReply`] cookie instructions.
///
/// Binding contract (plan §8.4):
/// - login flows answer with [`AuthFlowReply`]: cookie instructions are
///   returned on BOTH success and failure so cleanup is never lost;
/// - `complete_login` demands the REQUIRED browser binding (a rejected
///   consume still changes no cookies — the initiating browser keeps its
///   batch; anything after a successful consume always clears the
///   temporary challenge);
/// - session flows take [`PresentedSession`] instead of raw Headers:
///   parsing cookies is delivery's job, never the application's.
#[async_trait]
pub trait AuthService: Send + Sync {
    /// Issue the browser-bound provider authorize-URL batch: keeps the
    /// provider chain (subsequence) order and returns ONE
    /// `SetLoginChallenge` instruction carrying the fresh window.
    async fn login_urls(
        &self,
        request: BuildLoginUrls,
    ) -> AuthFlowReply<AuthProviderUrlList>;

    /// Complete the OAuth callback under the browser binding, in order:
    /// validate enabled provider + required code → consume the pending
    /// state → exchange the code → install the strict session.
    async fn complete_login(
        &self,
        command: CompleteOAuthLogin,
    ) -> AuthFlowReply<AuthRedirectLocation>;

    /// Project the already-authenticated Human caller; display-only.
    async fn current_user(
        &self,
        query: AuthenticatedUserQuery,
    ) -> Result<AuthUserInfo, ApplicationError>;

    /// Rotate the presented session; the rotated credential arrives as a
    /// `SetSession` cookie instruction.
    async fn refresh_session(
        &self,
        session: PresentedSession,
    ) -> AuthFlowReply<SessionRenewalData>;

    /// Revoke the presented session when there is one; `None` (no cookie)
    /// is a valid idempotent success boundary that skips the port.
    async fn logout(&self, session: Option<PresentedSession>) -> AuthFlowReply<LogoutData>;
}

/// The ONE Human projection for an ALREADY-authenticated caller (plan
/// §8.2): pure, HTTP-free, and shared by [`AuthService::current_user`]
/// implementations and delivery-side projections of verified callers (so
/// a deployment whose auth service is not configured — no OAuth —
/// projects identically to one that has it). Non-Human callers are not
/// projectable; there is no second chain execution behind this query and
/// `provider_label`/`avatar` are display data only, never authority.
pub fn project_current_user(
    query: &AuthenticatedUserQuery,
) -> Result<AuthUserInfo, ApplicationError> {
    let Some(user) = query.caller.user.as_ref() else {
        return Err(ApplicationError::Unauthenticated);
    };
    let name = user
        .display_name
        .clone()
        .or_else(|| user.full_name.clone())
        .or_else(|| Some(user.username.clone()));
    Ok(AuthUserInfo {
        user_id: user.id.clone(),
        name,
        provider: query.provider_label.clone(),
        avatar: query.avatar.clone(),
    })
}
