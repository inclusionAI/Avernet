//! Application-facing OAuth ports (V1 API auth plugin chain, Tasks 9/10).
//!
//! These ports are the ONLY surface the auth application layer consumes
//! for login: provider URL building/user exchange, strict session
//! install/refresh/revoke, and the browser-bound pending-login batches
//! (the service-side mirror of plugin-api's
//! `bcs_auth_api::login_state::PendingOAuthLoginStore`). Errors use the
//! transport-independent [`ApplicationError`] vocabulary so the
//! application layer never converts provider/store text of its own.
//!
//! Redaction (plan §8.4 credential-log red line): every type here that
//! can carry a credential (the batch with raw nonce/state pairs, the
//! session token) prints only its type name and elision markers — never
//! inner strings or provider URLs.

use async_trait::async_trait;

use crate::types::error::ApplicationError;

/// Normalized external identity of a completed provider login; the
/// strict session port turns it into a browser session (creating or
/// confirming the backing identity row).
#[derive(Clone)]
pub struct ExternalLoginIdentity {
    pub provider: String,
    pub external_user_id: String,
    pub name: Option<String>,
    pub avatar: Option<String>,
}

/// One issued pending-login batch for a single initiating browser
/// (service-side mirror of plugin-api's `LoginBatch`): the raw browser
/// nonce plus one `(provider, state)` pair per requested provider. The
/// first successful consume of any state invalidates the whole batch.
#[derive(Clone)]
pub struct PendingLoginBatch {
    pub browser_nonce: String,
    pub expires_at: u64,
    pub provider_states: Vec<(String, String)>,
}

/// New or refreshed login session credential handed to the delivery
/// layer for the `bcs_session` cookie; `expires_at` is the same instant
/// the strict session engine installed.
#[derive(Clone)]
pub struct BrowserSession {
    pub token: String,
    pub expires_at: u64,
}

// Plan §8.4 credential-log red line: the batch carries the raw nonce and
// one-time states, the session a bearer token — Debug shows type names
// and elision markers only.
impl std::fmt::Debug for PendingLoginBatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PendingLoginBatch").finish_non_exhaustive()
    }
}

impl std::fmt::Debug for BrowserSession {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BrowserSession").finish_non_exhaustive()
    }
}

impl std::fmt::Debug for ExternalLoginIdentity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ExternalLoginIdentity").finish_non_exhaustive()
    }
}

/// Provider-facing login port: enumerate enabled providers, build the
/// per-provider OAuth authorize URL binding the given state, and
/// exchange a one-time code for the external identity. Provider errors
/// surface as stable [`ApplicationError`] categories — never raw
/// third-party response text.
#[async_trait]
pub trait OAuthProviderPort: Send + Sync {
    /// Enabled provider names in chain order.
    fn names(&self) -> Vec<String>;

    /// Authorize URL for `provider` carrying `state`, using `redirect_uri`
    /// as the exact callback.
    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, ApplicationError>;

    /// Exchange the one-time `code` for the external identity.
    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError>;
}

/// Strict browser session port (Task 8 engine seam): install a session
/// for a completed login, rotate on refresh, revoke on logout. Revoke of
/// an already-replaced or unknown instance is an idempotent success.
#[async_trait]
pub trait OAuthSessionPort: Send + Sync {
    /// Install a NEW login session for the verified external identity.
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, ApplicationError>;

    /// Rotate the session carrying `token`.
    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, ApplicationError>;

    /// Revoke the session carrying `token`; both revoke outcomes
    /// (revoked / not current) are idempotent successes.
    async fn revoke(&self, token: &str) -> Result<(), ApplicationError>;
}

/// Browser-bound pending-login port, same semantics as the plugin-api
/// `PendingOAuthLoginStore` (plan §8.4): issuing a batch pairs a fresh
/// browser nonce with per-provider states; consume is an atomic
/// match-and-invalidate of the WHOLE batch. Store failures map to
/// [`ApplicationError`] categories and yield no usable URLs.
///
/// Byte-identity rule: the exact per-provider callback compared at
/// consume time is `{callback_base}/{provider}` exactly as it was stored
/// at issue time — EVERY caller (application orchestration, bridges,
/// delivery) derives it through the same join, with no side-specific
/// re-normalization, or the match will reject a genuine callback.
#[async_trait]
pub trait PendingOAuthLoginPort: Send + Sync {
    /// Issue a fresh pending-login batch under one flow namespace.
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError>;

    /// Atomically match-and-consume the batch owning `state`.
    async fn consume(
        &self,
        state: &str,
        browser_nonce: &str,
        provider: &str,
        exact_callback: &str,
        flow: &str,
        now: u64,
    ) -> Result<(), ApplicationError>;
}
