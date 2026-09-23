//! Application-facing OAuth ports (spec §8.4).
//!
//! Three ports live here, all consumed by the V1 secure-auth
//! application service ([`crate::application::v1::auth::SecureAuthService`]):
//!
//! - [`PendingOAuthLoginPort`] — the service-api layer's view of the
//!   browser-bound OAuth pending-login state store. The contract mirrors
//!   [`bcs_auth_api::PendingOAuthLoginStore`] (the plugin-API layer's
//!   view) field-for-field, but stays in `bcs-service-api` so the V1
//!   AuthService can depend on it without the service-api crate pulling
//!   the entire plugin-API surface (and so `bcs-auth-api` does not need
//!   to depend back on `bcs-service-api` — contract crates on both sides
//!   stay independent). The bootstrap (Task 11+) bridges a concrete
//!   plugin store into this port by translating every field. No
//!   `From`/`Into` impls are exposed across the crate boundary; the two
//!   types intentionally do NOT share an interface.
//! - [`OAuthProviderPort`] — the service-api layer's view of the OAuth
//!   provider chain (auth-URL building + code-for-user exchange). Mirrors
//!   the per-plugin [`bcs_auth_api::OAuthProvider`] trait surface but
//!   stays in `bcs-service-api` so the V1 AuthService never depends on
//!   the auth-plugin contract crate.
//! - [`OAuthSessionPort`] — install/refresh/revoke for the secure
//!   `BrowserSession`. The bootstrap (Task 11+) bridges the Task 8
//!   `OAuthSessionEngine` plus `AuthSessionIdentityPort::ensure_identity`
//!   into this port; the applatch layer never sees the engine or the
//!   identity-port trait.
//!
//! # Why service-api types instead of re-exporting the plugin-API ones
//!
//! The same rationale as `bcs-auth-api::session` vs
//! `bcs-service-api::port::repo::auth_session`: keep the two contract
//! crates' types independent so neither crate must release in lock-step
//! with the other. Bootstrap wiring translates each field; the V1
//! AuthService only sees the service-api types; the auth plugin only
//! sees the plugin-API types.
//!
//! # Atomicity rule (spec §8.4 "完成登录")
//!
//! See [`bcs_auth_api::PendingOAuthLoginStore`] for the full atomicity
//! rules (state lookup / expiry / constant-time nonce compare /
//! provider-callback-flow binding / atomic batch consumption under one
//! lock). The service-api port preserves the same semantics: a
//! mismatched consume MUST return [`ApplicationError`] and MUST NOT
//! consume the batch; a fully-matching consume MUST atomically
//! consume the entire batch. A failed [`PendingOAuthLoginPort::issue_batch`]
//! surfaces as [`ApplicationError::Unavailable`].

use async_trait::async_trait;

// `ApplicationError` is referenced via its full path
// `crate::application::v1::ApplicationError` at each signature instead of
// a top-level `use` import: P2 LINT-1 forbids importing from the
// application subtree inside `port/**`. Inlining keeps the cross-boundary
// dependency visible at each signature.

/// Service-api side pending-login batch: same shape as
/// [`bcs_auth_api::LoginBatch`] but an independent type so
/// `bcs-service-api` does not depend on the plugin-API crate. The
/// bootstrap bridge translates field-for-field at the seam.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PendingLoginBatch {
    /// The CSPRNG browser nonce (32 hex chars). Delivered to the
    /// browser via the `__Host-bcs_oauth_login` cookie. See the
    /// plugin-API type's rustdoc for the storage guarantees.
    pub browser_nonce: String,
    /// Unix-seconds absolute expiry. `now + 300` at issue time.
    pub expires_at: u64,
    /// Per-provider `(provider_name, state)` pairs.
    pub provider_states: Vec<(String, String)>,
}

/// Browser-bound pending OAuth login state store, service-api side.
///
/// Bridge implementations translate between this and
/// [`bcs_auth_api::PendingOAuthLoginStore`]. The V1 SecureAuthService
/// calls this port.
///
/// # Error taxonomy
///
/// Failed store operations surface as [`ApplicationError::Unavailable`];
/// state/expiry/provider/callback/flow nonce-hash mismatches surface
/// as [`ApplicationError`] (the bridge MAY use
/// [`ApplicationError::invalid`] /
/// [`ApplicationError::unauthenticated`] depending on whether the
/// caller is a login-initiate vs callback — Task 11 mapping). Either
/// way, the consume MUST NOT consume the batch on a mismatch.
#[async_trait]
pub trait PendingOAuthLoginPort: Send + Sync {
    /// Allocate a fresh browser nonce + per-provider state values.
    /// `exact_callback` per provider is `format!("{callback_base}/
    /// {provider}")` (the bridge computes it; the caller passes the
    /// base). TTL is fixed at 300 seconds.
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<PendingLoginBatch, crate::application::v1::ApplicationError>;

    /// Validate and atomically consume a pending-login record (and
    /// its whole batch on a full match). See the trait-level docs and
    /// [`bcs_auth_api::PendingOAuthLoginStore::consume`] for the
    /// atomicity rules.
    async fn consume(
        &self,
        state: &str,
        browser_nonce: &str,
        provider: &str,
        exact_callback: &str,
        flow: &str,
        now: u64,
    ) -> Result<(), crate::application::v1::ApplicationError>;
}

/// Service-api side view of an OAuth-provider-issued external login
/// identity. Same shape as the plugin-API layer's
/// `ProviderUserInfo` (post token-exchange) but an independent type so
/// `bcs-service-api` does not depend on `bcs-auth-api`. The bootstrap
/// bridge builds this from the OAuth provider chain's
/// [`bcs_auth_api::OAuthProvider::exchange_code`] +
/// [`bcs_auth_api::OAuthProvider::get_user_info`] calls.
///
/// Carries the provider name (the chain subsequence label), the
/// provider-stable external user id, and optional display fields. The
/// `external_user_id` is the seed for the internal `user_id` derived by
/// `AuthSessionIdentityPort::ensure_identity`; the application layer
/// never authorizes on `external_user_id` — authorization goes through
/// the internal `user_id` written into the signed JWT.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExternalLoginIdentity {
    pub provider: String,
    pub external_user_id: String,
    pub name: Option<String>,
    pub avatar: Option<String>,
}

/// Service-api side view of a freshly issued/rotated browser session.
/// Same shape as the plugin-API layer's
/// [`bcs_auth_api::IssuedSession`] but an independent type. The
/// bootstrap bridge builds this from the Task 8
/// `OAuthSessionEngine::install`/`refresh` outcomes.
///
/// Carries only the signed token and its absolute `expires_at`; the
/// delivery adapter translates it into a `SetSession` cookie change via
/// [`crate::application::v1::auth::BrowserCookieChange::SetSession`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BrowserSession {
    pub token: String,
    pub expires_at: u64,
}

/// OAuth provider chain port (service-api side). Bridges the
/// provider-rate-chain surface from the plugin-API layer into the V1
/// SecureAuthService. The bridge gets the chain once at construction
/// time and delegates `auth_url`/`exchange_user` calls field-by-field.
///
/// `names` returns the chain's provider subsequence order (the order
/// `login_urls` MUST preserve in its reply). `auth_url` builds a single
/// provider's authorize URL with the state and `redirect_uri` already
/// composed by the application. `exchange_user` does the
/// `exchange_code` + `get_user_info` round-trip in one call and returns
/// the normalized external identity.
#[async_trait]
pub trait OAuthProviderPort: Send + Sync {
    /// The chain's provider subsequence, in the configured order.
    async fn names(&self) -> Vec<String>;

    /// Build the authorize URL for `provider` with the pending-state
    /// value and exact `redirect_uri` (= `{callback_base}/{provider}`).
    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, crate::application::v1::ApplicationError>;

    /// Exchange `code` for tokens and fetch the normalized external
    /// identity from the provider. The bridge hides the
    /// `exchange_code` + `get_user_info` round-trip so the application
    /// layer never carries the intermediate `OAuthToken`.
    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, crate::application::v1::ApplicationError>;
}

/// Browser-session lifecycle port (service-api side). Bridges the Task
/// 8 `OAuthSessionEngine` + `AuthSessionIdentityPort::ensure_identity`
/// surface into the V1 SecureAuthService. The bridge owns the env
/// partition and the JWT secret; the application layer drives the
/// high-level lifecycle via this port.
///
/// `install_identity` is called once per successful OAuth exchange —
/// the bridge first calls `ensure_identity` with the external
/// identity's `(provider, external_user_id, name?, avatar?)` to obtain
/// the internal `user_id`, then calls `OAuthSessionEngine::install` to
/// issue a fresh JWT + CAS-install the binding. `refresh` rotates the
/// token (`OAuthSessionEngine::refresh`); `revoke` always returns
/// idempotent success regardless of the underlying store's
/// `Revoked`/`NotCurrent` outcome.
#[async_trait]
pub trait OAuthSessionPort: Send + Sync {
    /// Install a fresh session for the external identity and return the
    /// signed token + expiry. The bridge handles `ensure_identity` +
    /// `OAuthSessionEngine::install` translation; the application layer
    /// only sees the resulting [`BrowserSession`].
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, crate::application::v1::ApplicationError>;

    /// Refresh a presented session token and return the new token +
    /// expiry. The bridge handles `OAuthSessionEngine::refresh`
    /// translation.
    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, crate::application::v1::ApplicationError>;

    /// Revoke a presented session token. Both store outcomes
    /// (`Revoked`/`NotCurrent`) are idempotent SUCCESS at this layer —
    /// the application layer does NOT distinguish them.
    async fn revoke(&self, token: &str) -> Result<(), crate::application::v1::ApplicationError>;
}
