use std::fmt;

use async_trait::async_trait;
use serde::Serialize;

use crate::application::RequestAuthHeaders;

use super::ApplicationError;

/// REQUIRED browser binding of every OAuth callback (plan §8.4): the
/// nonce the temporary login-challenge cookie delivered to the browser
/// that initiated the login. A nonce in a query parameter can never
/// substitute for this binding.
#[derive(Clone)]
pub struct BrowserLoginBinding {
    pub nonce: String,
}

/// Verified OAuth callback command carrying the initiating browser's
/// REQUIRED binding (plan §8.4). Coexists with the legacy
/// [`CompleteOAuthLogin`] until Task 11 atomically swaps the active
/// `AuthService` surface; new code must construct a real
/// [`BrowserLoginBinding`] — there is no `Option` escape hatch.
#[derive(Clone)]
pub struct BoundOAuthLogin {
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
#[derive(Clone)]
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

impl fmt::Debug for BoundOAuthLogin {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("BoundOAuthLogin").finish_non_exhaustive()
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

#[derive(Debug, Clone, Serialize)]
pub struct AuthProviderUrl {
    pub name: String,
    pub url: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct AuthProviderUrlList {
    pub providers: Vec<AuthProviderUrl>,
}

#[derive(Debug, Clone)]
pub struct CompleteOAuthLogin {
    pub provider: String,
    pub code: Option<String>,
    pub auth_code: Option<String>,
    pub state: String,
    pub callback_base_url: String,
}

#[derive(Debug, Clone)]
pub struct ReadCurrentUser {
    pub headers: RequestAuthHeaders,
}

#[derive(Debug, Clone)]
pub struct RefreshSession {
    pub headers: RequestAuthHeaders,
}

#[derive(Debug, Clone)]
pub struct LogoutSession {
    pub headers: RequestAuthHeaders,
}

#[derive(Debug, Clone, Serialize)]
pub struct AuthUserInfo {
    pub user_id: String,
    pub name: Option<String>,
    pub provider: String,
    pub avatar: Option<String>,
}

#[derive(Debug, Clone)]
pub struct AuthRedirect {
    pub location: String,
    pub set_cookie: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct SessionRenewal {
    #[serde(skip_serializing)]
    pub set_cookie: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct LogoutResult {
    #[serde(skip_serializing)]
    pub set_cookie: String,
}

#[async_trait]
pub trait AuthService: Send + Sync {
    async fn login_urls(
        &self,
        request: BuildLoginUrls,
    ) -> Result<AuthProviderUrlList, ApplicationError>;

    async fn complete_login(
        &self,
        request: CompleteOAuthLogin,
    ) -> Result<AuthRedirect, ApplicationError>;

    async fn current_user(&self, request: ReadCurrentUser)
        -> Result<AuthUserInfo, ApplicationError>;

    async fn refresh_session(&self, request: RefreshSession)
        -> Result<SessionRenewal, ApplicationError>;

    async fn logout(&self, request: LogoutSession) -> Result<LogoutResult, ApplicationError>;
}
