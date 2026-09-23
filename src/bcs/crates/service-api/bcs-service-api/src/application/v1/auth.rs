use async_trait::async_trait;
use serde::Serialize;

use super::identity::AuthenticatedCaller;
use super::ApplicationError;

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

/// Browser-bound OAuth callback command (spec §8.4).
///
/// This is the FINAL Task 11 shape of the login command (the Task 10
/// temporary `BoundOAuthLogin` name was retired in the atomic switchover;
/// there is no legacy Option-code variant anymore). `browser_binding` is
/// REQUIRED — the delivery adapter extracts the `__Host-bcs_oauth_login`
/// cookie nonce and passes it here as a non-`Option`, non-`Default`-able
/// value. There is no query-string fallback: an empty/missing cookie is
/// rejected by the adapter BEFORE this command is constructed.
///
/// `code` is REQUIRED (promoted from the old `Option<String>` shape in
/// this switchover): a callback without an authorization code cannot
/// start token exchange. `auth_code` remains optional (only some
/// providers, e.g. Alipay, use that parameter instead of `code`).
#[derive(Debug, Clone)]
pub struct CompleteOAuthLogin {
    pub provider: String,
    pub code: String,
    pub auth_code: Option<String>,
    pub state: String,
    pub callback_base_url: String,
    pub browser_binding: BrowserLoginBinding,
}

/// The browser-side nonce binding of a pending OAuth login (spec §8.4).
///
/// Constructed by the delivery adapter from the `__Host-bcs_oauth_login`
/// cookie. The AuthService passes it straight to
/// [`crate::port::oauth::PendingOAuthLoginPort::consume`] along with
/// the state, provider, exact callback, and flow namespace so the
/// pending-login store can hash-and-compare it against the stored
/// nonce hash in constant time.
///
/// Required, non-`Option`, non-`Default`-able: every callback path
/// MUST present this value, and any test double that constructs a
/// login command MUST provide a real binding (no default-empty
/// bypass).
#[derive(Debug, Clone)]
pub struct BrowserLoginBinding {
    pub nonce: String,
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

/// Cookie mutations an auth flow may request from the delivery adapter
/// (spec §8.4).
///
/// A single callback can issue a session Set-Cookie AND clear the
/// temporary login challenge cookie (the temporary cookie is cleared on
/// ANY matched consume, success or follow-up failure; the session cookie
/// is set ONLY on a fully successful login). Login URL building issues a
/// `SetLoginChallenge`; logout issues `ClearSession`; refresh issues
/// `SetSession`; init/legacy errors issue `ClearLoginChallenge`.
///
/// The variants are pure data: no axum/`SetCookie`/header types here.
/// The delivery adapter translates each variant to the actual
/// `Set-Cookie` header (with `__Host-bcs_oauth_login` /
/// `bcs_session` canonical names; `Path=/; HttpOnly; Secure;
/// SameSite=Lax` for the challenge; same for the session plus the
/// `Max-Age` from the expires_at).
#[derive(Debug)]
pub enum BrowserCookieChange {
    /// Set the temporary login-challenge cookie with a fresh CSPRNG
    /// nonce; `expires_at = now + 300s`. `__Host-bcs_oauth_login`.
    SetLoginChallenge { nonce: String, expires_at: u64 },
    /// Clear the temporary login-challenge cookie after a matched
    /// callback or on a pre-matched error.
    ClearLoginChallenge,
    /// Set the session cookie with a freshly signed JWT; `expires_at`
    /// matches the JWT `exp` claim. `bcs_session`.
    SetSession { token: String, expires_at: u64 },
    /// Clear the session cookie (logout).
    ClearSession,
}

/// Transport-agnostic reply of an auth flow with explicit cookie
/// mutation instructions (spec §8.4 "契约传播").
///
/// Both success (`Ok`) and failure (`Err`) paths can carry cleanup
/// instructions: the calling adapter MUST apply cookie_changes
/// unconditionally (in the order the flow produced), independent of
/// the inner `result`. This is what keeps the temporary login challenge
/// cookie cleared on matched-but-subsequently-failed callbacks (the
/// matched consume must always clear, even if the exchange/write
/// step that follows blows up).
///
/// The type carries NO HTTP response/header types — pure data. The
/// delivery adapter converts each [`BrowserCookieChange`] into the
/// corresponding `Set-Cookie` header.
#[derive(Debug)]
pub struct AuthFlowReply<T> {
    pub result: Result<T, ApplicationError>,
    pub cookie_changes: Vec<BrowserCookieChange>,
}

/// Required, non-`Option`, non-`Default`-able presented-session token
/// consumed by [`AuthService::refresh_session`] /
/// [`AuthService::logout`].
///
/// The delivery adapter extracts the `bcs_session` cookie and passes
/// its value here as `token`. The application layer NEVER parses cookies:
/// an empty/missing session cookie is rejected at the adapter before this
/// command is constructed. There is no fallback to a `Headers` blob —
/// the application layer only handles the token (and a None variant on
/// logout, which is the valid "no session presented" idempotent boundary).
#[derive(Debug, Clone)]
pub struct PresentedSession {
    pub token: String,
}

/// Query for [`AuthService::current_user`].
///
/// Only projects an already-authenticated Human from the caller; no
/// re-authentication, no second Identity chain pass. `provider_label`
/// and `avatar` are TRUSTED display data injected by the delivery adapter
/// from the upstream authenticated principal (Gateway / OAuth provider) —
/// display-only, MUST NEVER be used as authorization. The delivery-tier
/// `AuthenticationContext` never reaches business code.
///
/// `Avatar` is `Option<String>` to mirror [`AuthUserInfo::avatar`]; pass
/// `None` if the upstream identity carried no avatar artifact. The
/// `provider_label` is required (the legacy `/auth/user` shape always
/// returned a provider field).
#[derive(Debug, Clone)]
pub struct AuthenticatedUserQuery {
    pub caller: AuthenticatedCaller,
    pub provider_label: String,
    pub avatar: Option<String>,
}

/// Transport-agnostic secure-auth application service.
///
/// Since the Task 10/11 atomic switchover this trait carries the STABLE
/// name `AuthService` and the secure (browser-bound, cookie-change)
/// semantics — the pre-switchover trait and its temporary
/// `SecureAuthService` alias no longer exist. BOTH the V1 auth
/// entrypoint (`bcs-api-http`) and the legacy `/auth/*` entrypoint
/// (`bcs-http` oauth routes) consume this one trait; there are no dual
/// production entrypoints.
///
/// Implementations orchestrate the OAuth login flow via injected
/// [`crate::port::oauth::OAuthProviderPort`],
/// [`crate::port::oauth::OAuthSessionPort`], and
/// [`crate::port::oauth::PendingOAuthLoginPort`] ports plus a small set of
/// config-injected values (enabled provider chain, configured flow
/// namespace, configured post-login redirect). NO HTTP types, NO cookie
/// parsing, NO secret resolution, NO provider selection logic, and NO
/// origin checks — those concerns live in the delivery adapter or the
/// bootstrap bridges, never in this trait or its impl.
///
/// All flow methods return [`AuthFlowReply<T>`]: the cookie mutation
/// instructions are explicit and the delivery adapter applies them
/// unconditionally regardless of the inner `result`. See
/// [`AuthFlowReply`] and [`BrowserCookieChange`] for the contract.
#[async_trait]
pub trait AuthService: Send + Sync {
    /// Build the login-URL list in the configured provider chain
    /// subsequence order, issue a fresh pending-login batch (cookie
    /// challenge), and reply the URL list plus a `SetLoginChallenge`
    /// cookie change. A failed pending store surfaces as an error
    /// reply with NO cookie changes (no challenge to clear — the
    /// challenge was never set).
    async fn login_urls(
        &self,
        command: BuildLoginUrls,
    ) -> AuthFlowReply<Vec<AuthProviderUrl>>;

    /// Complete a browser-bound OAuth callback. Validates the
    /// enabled provider and required `code`, atomically consumes the
    /// pending-login batch (binding a state + nonce + provider + exact
    /// callback + flow), exchanges the code for an
    /// [`crate::port::oauth::ExternalLoginIdentity`], installs the
    /// identity into the session port, and replies the redirect to the
    /// configured post-login URL with a `SetSession` +
    /// `ClearLoginChallenge` cookie change pair. A binding failure
    /// (consume `Err`) burns nothing and clears nothing. Failure
    /// AFTER a successful consume (exchange or install) replies the
    /// error AND `ClearLoginChallenge` per spec §8.4 (the matched
    /// callback's challenge is always cleared).
    async fn complete_login(&self, command: CompleteOAuthLogin) -> AuthFlowReply<AuthRedirect>;

    /// Refresh a presented session token. The session port returns a
    /// freshly rotated token; the reply carries a `SetSession` cookie
    /// change. An error reply carries NO cookie changes (the
    /// delivery adapter then clears the session cookie per its
    /// own 401 mapping).
    async fn refresh_session(&self, command: PresentedSession)
        -> AuthFlowReply<SessionRenewal>;

    /// Revoke and clear the session. `Some(token)` calls the session
    /// port's `revoke` (both store outcomes Revoked/NotCurrent are
    /// idempotent success); `None` is the valid "no session
    /// presented" idempotent logout boundary (no revoke call is made).
    /// Either path replies success and a single `ClearSession` cookie
    /// change.
    async fn logout(&self, command: Option<PresentedSession>) -> AuthFlowReply<()>;

    /// Project the already-authenticated Human principal into the
    /// /auth/user shape. `provider_label` and `avatar` from the
    /// adapter are display-only (NEVER authorization). Returns an
    /// [`ApplicationError`] directly — never an [`AuthFlowReply`]
    /// because `current_user` does not mutate cookies.
    async fn current_user(&self, query: AuthenticatedUserQuery)
        -> Result<AuthUserInfo, ApplicationError>;
}
