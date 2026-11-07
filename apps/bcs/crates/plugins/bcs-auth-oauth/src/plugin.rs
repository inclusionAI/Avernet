//! Provider-agnostic OAuth session `AuthPlugin`.
//!
//! Authenticates a request by verifying the `bcs_session` JWT cookie. It is
//! **not** tied to any single provider: the issuing provider is recorded in
//! the JWT (`claims.src`) at login time, so one instance handles google,
//! github, or any future provider. Provider-specific code (token exchange,
//! userinfo) lives in each [`bcs_auth_api::OAuthProvider`] implementation,
//! not here.
//!
//! The plugin delegates to an INJECTED [`crate::OAuthSessionEngine`] (the
//! strict construction from the Task 8 lifecycle): signature, injected env,
//! hash binding, sid/revision, and revocation are all enforced, so every
//! accepted session was issued through the engine's install lifecycle —
//! the path the shared (legacy + V1) login entries use.

use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;

use bcs_auth_api::{extract_session_cookie, AuthError, AuthPlugin, AuthPrincipal};

use crate::strict::OAuthSessionEngine;
use crate::verify::verify_oauth_session;

/// Session-cookie `AuthPlugin` shared by all OAuth providers.
///
/// Priority 25 places it between `cookie` (20) and `session` (30).
pub struct OAuthSessionPlugin {
    engine: Arc<OAuthSessionEngine>,
    priority: u8,
}

impl OAuthSessionPlugin {
    /// Default chain priority (lower runs earlier).
    pub const DEFAULT_PRIORITY: u8 = 25;

    /// STRICT construction (the ONLY construction since the Task 11
    /// entrypoint migration): the plugin delegates verification to the
    /// injected [`OAuthSessionEngine`] (signature + injected env + hash
    /// binding + sid/revision + revocation) and builds the principal from
    /// the trusted snapshot. Legacy sid-less JWTs are rejected on this
    /// path — sessions must be issued via the engine's lifecycle.
    pub fn new_with_session_engine(engine: Arc<OAuthSessionEngine>) -> Self {
        Self {
            engine,
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
        verify_oauth_session(&self.engine, headers).await
    }

    fn priority(&self) -> u8 {
        self.priority
    }

    fn name(&self) -> &'static str {
        "oauth_session"
    }
}
