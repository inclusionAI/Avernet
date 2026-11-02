//! Provider-agnostic OAuth session `AuthPlugin`.
//!
//! Authenticates a request by verifying the `bcs_session` JWT cookie. It is
//! **not** tied to any single provider: the issuing provider is recorded in
//! the JWT (`claims.src`) at login time, so one instance handles google,
//! github, or any future provider. Provider-specific code (token exchange,
//! userinfo) lives in each [`bcs_auth_api::OAuthProvider`] implementation,
//! not here.
//!
//! Two construction paths exist during the V1 API auth migration:
//!
//! - [`OAuthSessionPlugin::new_with_session_engine`] — the STRICT path: the
//!   plugin delegates to the injected [`crate::OAuthSessionEngine`], which
//!   enforces signature, injected env, hash binding, sid/revision, and
//!   revocation. This is the path bootstrap wiring migrates to.
//! - [`OAuthSessionPlugin::new`] — the **UNSAFE-CONSTRUCTION TRANSITION
//!   SEAM** (deleted when the HTTP entrypoints are migrated, Task 11): it
//!   keeps the pre-strict legacy behavior for callers not yet injected with
//!   a strict identity session port. It is marked so the migration can find
//!   every remaining constructor by grepping `unsafe_construction_transition`.

use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;

use bcs_auth_api::{extract_session_cookie, AuthError, AuthPlugin, AuthPrincipal, UserIdentityPort};

use crate::strict::OAuthSessionEngine;
use crate::verify::{
    verify_oauth_session, verify_oauth_session_unsafe_construction_transition,
};

/// Which session backend this plugin instance consults.
enum SessionBackend {
    /// Strict engine delegation (the new chain's path).
    Strict(Arc<OAuthSessionEngine>),
    /// UNSAFE-CONSTRUCTION TRANSITION SEAM (Task 11 deletes this variant
    /// together with [`OAuthSessionPlugin::new`]).
    UnsafeConstructionTransition {
        jwt_secret: String,
        user_port: Arc<dyn UserIdentityPort>,
    },
}

/// Session-cookie `AuthPlugin` shared by all OAuth providers.
///
/// Priority 25 places it between `cookie` (20) and `session` (30).
pub struct OAuthSessionPlugin {
    backend: SessionBackend,
    priority: u8,
}

impl OAuthSessionPlugin {
    /// Default chain priority (lower runs earlier).
    pub const DEFAULT_PRIORITY: u8 = 25;

    /// STRICT construction: the plugin delegates verification to the
    /// injected [`OAuthSessionEngine`] (signature + injected env + hash
    /// binding + sid/revision + revocation) and builds the principal from
    /// the trusted snapshot. Legacy sid-less JWTs are rejected on this
    /// path — sessions must be issued via the engine's lifecycle.
    pub fn new_with_session_engine(engine: Arc<OAuthSessionEngine>) -> Self {
        Self {
            backend: SessionBackend::Strict(engine),
            priority: Self::DEFAULT_PRIORITY,
        }
    }

    /// UNSAFE-CONSTRUCTION TRANSITION SEAM — ** scheduled for DELETION in
    /// the HTTP entrypoint migration (Task 11)**.
    ///
    /// Pre-strict construction: the plugin verifies legacy sid-less JWTs
    /// against the token-hash binding kept by [`UserIdentityPort`]. Kept so
    /// not-yet-migrated wiring keeps compiling and running during the
    /// transition; every remaining call site is discoverable by grepping
    /// `unsafe_construction_transition`. Bootstrap wiring migrates to
    /// [`OAuthSessionPlugin::new_with_session_engine`].
    pub fn new(jwt_secret: impl Into<String>, user_port: Arc<dyn UserIdentityPort>) -> Self {
        Self {
            backend: SessionBackend::UnsafeConstructionTransition {
                jwt_secret: jwt_secret.into(),
                user_port,
            },
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
        match &self.backend {
            SessionBackend::Strict(engine) => verify_oauth_session(engine, headers).await,
            SessionBackend::UnsafeConstructionTransition {
                jwt_secret,
                user_port,
            } => {
                verify_oauth_session_unsafe_construction_transition(
                    headers,
                    jwt_secret,
                    user_port.as_ref(),
                )
                .await
            }
        }
    }

    fn priority(&self) -> u8 {
        self.priority
    }

    fn name(&self) -> &'static str {
        "oauth_session"
    }
}
