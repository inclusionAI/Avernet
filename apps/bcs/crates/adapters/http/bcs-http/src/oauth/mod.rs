//! OAuth HTTP routes: /auth/url, /auth/callback/{provider}, /auth/logout.
//!
//! These routes are shared across all OAuth providers. Provider-specific
//! logic is delegated to the `OAuthProvider` trait implementations injected
//! into `OAuthRouteState`.
//!
//! Route ownership is split by responsibility: the HTTP handlers live in
//! [`routes`] and the V1 `AuthService` facade over the shared state lives in
//! [`legacy_facade`]. This module owns `OAuthRouteState` and the
//! session-cookie helpers both of them share.

mod legacy_facade;
pub mod state;
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

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use bcs_auth_api::{BCS_SESSION_COOKIE, OAuthConfig, OAuthProvider, UserIdentityPort};
use bcs_jwt::JwtService;

use crate::oauth::state::OAuthStateStore;

// Names this module no longer uses itself but that `tests.rs` /
// `diagnostics_test.rs` reach through their `use super::*` glob imports.
// Keep in sync with those files; behavior-preserving boundary split.
#[allow(unused_imports)]
use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
#[allow(unused_imports)]
use bcs_jwt::Claims;
#[allow(unused_imports)]
use bcs_service_api::application::RequestAuthHeaders;
#[allow(unused_imports)]
use bcs_service_api::application::v1::{
    ApplicationError, AuthService, BuildLoginUrls, CompleteOAuthLogin, LogoutSession,
    ReadCurrentUser, RefreshSession,
};

/// Shared state for OAuth routes.
pub struct OAuthRouteState {
    pub jwt_service: JwtService,
    pub user_port: Arc<dyn UserIdentityPort>,
    pub providers: HashMap<String, Arc<dyn OAuthProvider>>,
    pub state_store: OAuthStateStore,
    pub config: OAuthConfig,
    /// Non-OAuth fallback identity source. When a request has no valid
    /// `bcs_session` cookie (or OAuth is not configured at all),
    /// `current_user_handler` resolves the caller via this chain — e.g. the
    /// local mock plugin. `None` only in contract-test state that never serves
    /// real traffic.
    pub auth_chain: Option<Arc<bcs_auth_api::AuthPluginChain>>,
}


impl OAuthRouteState {
    pub fn new(
        jwt_secret: &str,
        user_port: Arc<dyn UserIdentityPort>,
        providers: HashMap<String, Arc<dyn OAuthProvider>>,
        config: OAuthConfig,
        auth_chain: Option<Arc<bcs_auth_api::AuthPluginChain>>,
    ) -> Self {
        Self {
            jwt_service: JwtService::new(jwt_secret),
            user_port,
            providers,
            state_store: OAuthStateStore::new(Duration::from_secs(300)), // 5 min TTL
            config,
            auth_chain,
        }
    }

    /// Identity-only state for the no-OAuth case: `/auth/user` is backed solely
    /// by the auth chain. The `JwtService` is unused on this path — the cookie
    /// lookup is only attempted when `config.jwt_secret` is non-empty, which this
    /// state leaves empty, so no cookie is ever verified here.
    pub fn new_chain_only(
        user_port: Arc<dyn UserIdentityPort>,
        auth_chain: Arc<bcs_auth_api::AuthPluginChain>,
    ) -> Self {
        Self {
            jwt_service: JwtService::new(""),
            user_port,
            providers: HashMap::new(),
            state_store: OAuthStateStore::new(Duration::from_secs(300)), // 5 min TTL
            config: OAuthConfig::default(),
            auth_chain: Some(auth_chain),
        }
    }
}

/// Build the `Set-Cookie` value carrying the session JWT. `secure` controls the
/// `Secure` attribute — must be `false` over plain HTTP (local dev) or browsers
/// drop the cookie.
fn session_cookie(jwt: &str, secure: bool) -> String {
    let secure_attr = if secure { "; Secure" } else { "" };
    format!("{BCS_SESSION_COOKIE}={jwt}; HttpOnly{secure_attr}; SameSite=Lax; Path=/")
}

/// Build the `Set-Cookie` value that clears the session cookie.
fn clear_session_cookie(secure: bool) -> String {
    let secure_attr = if secure { "; Secure" } else { "" };
    format!("{BCS_SESSION_COOKIE}=; HttpOnly{secure_attr}; SameSite=Lax; Path=/; Max-Age=0")
}
