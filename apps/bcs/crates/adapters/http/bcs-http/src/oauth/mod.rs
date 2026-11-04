//! Legacy `/auth/*` HTTP entry (V1 API auth plan, Task 11).
//!
//! This state is now a THIN holder: the injected secure
//! [`AuthService`] (the same transport-neutral orchestration the V1
//! entry uses, built with the LEGACY flow namespace and the same
//! pending-state / session ports) owns every callback / refresh /
//! logout business decision. The handlers in [`routes`] only parse the
//! temporary login-challenge cookie, the session cookie, the request
//! method, and the request Origin, then consume the reply's
//! [`AuthFlowReply`] cookie instructions as separate appended
//! `Set-Cookie` headers. The retained local readers are the non-OAuth
//! `/auth/user` chain fallback and the `/auth/user/{user_id}` display
//! lookup — both resolve the caller through the injected auth chain (the
//! strict delegated session verification) and neither re-implements flow
//! business logic or JWT decoding.
//!
//! Cookie contract (plan §8.4): the production login challenge is the
//! host-only `__Host-bcs_oauth_login` cookie (Path=/, HttpOnly, Secure,
//! SameSite=Lax, Max-Age=300) and plain-HTTP local development uses a
//! different, non-`__Host` name — production never downgrades. The
//! session cookie keeps the existing `bcs_session` name and flags.
//!
//! CSRF (plan §8.3 — compatibility tightening, documented): the legacy
//! refresh/logout POST are cookie-authenticated unsafe writes. Their
//! trusted-origin set is injected by bootstrap from the finite exact
//! subset of `cors.allowed_origins` (wildcards and invalid entries are
//! excluded); with no exact origins configured, cookie unsafe requests
//! are REJECTED. Origins are never inferred from Host or Referer, and a
//! Gateway identity never substitutes for the cookie.

mod routes;

#[cfg(test)]
mod diagnostics_test;
#[cfg(test)]
mod tests;

pub use routes::{
    AuthUrlResponse, CallbackParams, ProviderUrl, UserInfoResponse, auth_url_handler,
    callback_handler, current_user_handler, get_user_handler, identity_routes, logout_handler,
    refresh_handler, routes,
};

use std::sync::Arc;

use bcs_auth_api::{AuthPluginChain, OAuthConfig, UserIdentityPort};
use bcs_service_api::application::v1::AuthService;

/// The cookie name pair for the login challenge (plan §8.4): production
/// uses the host-only `__Host-` name; plain-HTTP local development uses
/// a different name because browsers drop `Secure` cookies over HTTP —
/// and the production name must never learn to work without `Secure`.
pub const HOST_CHALLENGE_COOKIE: &str = "__Host-bcs_oauth_login";
pub const LOCAL_CHALLENGE_COOKIE: &str = "bcs_oauth_login";
/// Pinned challenge window (plan §8.4: 5 minutes).
pub const CHALLENGE_MAX_AGE_SECS: u64 = 300;

/// Shared state for the legacy `/auth/*` routes: an injected secure
/// `AuthService` plus the legacy identity readers. No flow business
/// logic lives here.
pub struct OAuthRouteState {
    /// The injected secure orchestration (LEGACY flow namespace). `None`
    /// only for the identity-only mount that serves `/auth/user` alone.
    pub auth_service: Option<Arc<dyn AuthService>>,
    /// Legacy identity reader for the non-OAuth `/auth/user` path.
    pub auth_chain: Option<Arc<AuthPluginChain>>,
    /// Legacy display reader for `/auth/user/{user_id}`.
    pub user_port: Arc<dyn UserIdentityPort>,
    pub config: OAuthConfig,
    /// Exact trusted origins for cookie-authenticated unsafe writes,
    /// injected by bootstrap (derived from `cors.allowed_origins`).
    pub trusted_origins: Vec<String>,
}

impl OAuthRouteState {
    pub fn new(
        auth_service: Arc<dyn AuthService>,
        user_port: Arc<dyn UserIdentityPort>,
        config: OAuthConfig,
        auth_chain: Option<Arc<AuthPluginChain>>,
        trusted_origins: Vec<String>,
    ) -> Self {
        Self {
            auth_service: Some(auth_service),
            auth_chain,
            user_port,
            config,
            trusted_origins,
        }
    }

    /// Identity-only state for the no-OAuth case: `/auth/user` is backed
    /// solely by the auth chain (no `bcs_session` cookie is ever verified
    /// without OAuth), and no login family route is mounted with this
    /// state.
    pub fn new_chain_only(
        user_port: Arc<dyn UserIdentityPort>,
        auth_chain: Arc<AuthPluginChain>,
    ) -> Self {
        Self {
            auth_service: None,
            auth_chain: Some(auth_chain),
            user_port,
            config: OAuthConfig::default(),
            trusted_origins: Vec::new(),
        }
    }

    /// Login-challenge transport is pinned to the public URL, not the
    /// independent legacy session-cookie Secure override.
    pub(super) fn challenge_cookie_secure(&self) -> bool {
        OAuthConfig::default_cookie_secure(&self.config.base_url)
    }

    /// The injected secure service for the mounted login family routes.
    pub fn require_auth_service(&self) -> Option<&Arc<dyn AuthService>> {
        self.auth_service.as_ref()
    }
}

/// The one challenge cookie name for a transport mode. EXACTLY ONE name is
/// valid per mode (plan §8.4): production HTTPS honors only the
/// host-only `__Host-` name (subdomains cannot forge host-only cookies,
/// closing the Login CSRF hole of a `Domain`-scoped local-name cookie),
/// and plain-HTTP local development uses the separate non-`__Host` name
/// (a `Secure` cookie would be dropped there). The OTHER mode's name is
/// never accepted.
pub fn challenge_cookie_name(secure: bool) -> &'static str {
    if secure {
        HOST_CHALLENGE_COOKIE
    } else {
        LOCAL_CHALLENGE_COOKIE
    }
}
