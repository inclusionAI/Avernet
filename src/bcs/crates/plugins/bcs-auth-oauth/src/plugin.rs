//! Provider-agnostic OAuth session `AuthPlugin`.
//!
//! Authenticates a request by verifying the `bcs_session` JWT cookie via
//! [`verify_oauth_session`]. It is **not** tied to any single provider: the
//! issuing provider is recorded in the JWT (`claims.src`) at login time, so one
//! instance of this plugin handles google, github, or any future provider. The
//! provider-specific code (token exchange, userinfo) lives in each
//! [`bcs_auth_api::OAuthProvider`] implementation, not here.

use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;

use bcs_auth_api::{extract_session_cookie, AuthError, AuthPlugin, AuthPrincipal, UserIdentityPort};

use crate::verify::verify_oauth_session;

/// Session-cookie `AuthPlugin` shared by all OAuth providers.
///
/// Priority 25 places it between `cookie` (20) and `session` (30).
pub struct OAuthSessionPlugin {
    jwt_secret: String,
    user_port: Arc<dyn UserIdentityPort>,
    priority: u8,
}

impl OAuthSessionPlugin {
    /// Default chain priority (lower runs earlier).
    pub const DEFAULT_PRIORITY: u8 = 25;

    pub fn new(jwt_secret: impl Into<String>, user_port: Arc<dyn UserIdentityPort>) -> Self {
        Self {
            jwt_secret: jwt_secret.into(),
            user_port,
            priority: Self::DEFAULT_PRIORITY,
        }
    }

    /// Override the chain priority (lower runs earlier).
    pub fn with_priority(mut self, priority: u8) -> Self {
        self.priority = priority;
        self
    }
}

#[async_trait]
impl AuthPlugin for OAuthSessionPlugin {
    fn can_authenticate(&self, headers: &HeaderMap) -> bool {
        extract_session_cookie(headers).is_some()
    }

    async fn authenticate(
        &self,
        headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        verify_oauth_session(headers, &self.jwt_secret, self.user_port.as_ref()).await
    }

    fn priority(&self) -> u8 {
        self.priority
    }

    fn name(&self) -> &'static str {
        "oauth_session"
    }
}
