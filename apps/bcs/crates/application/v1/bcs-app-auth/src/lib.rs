//! Versioned secure-auth application facade for the BCN V1 API.
//!
//! [`AuthApplicationService`] implements
//! [`bcs_service_api::application::v1::auth::AuthService`] by
//! orchestrating three transport-neutral ports plus a small
//! config-injected bundle:
//!
//! - [`bcs_service_api::port::oauth::OAuthProviderPort`] for building
//!   authorize URLs and exchanging codes for normalized external
//!   identities.
//! - [`bcs_service_api::port::oauth::OAuthSessionPort`] for installing,
//!   refreshing, and revoking browser sessions.
//! - [`bcs_service_api::port::oauth::PendingOAuthLoginPort`] for the
//!   browser-bound pending-login state store (spec §8.4 atomicity).
//!
//! The facade owns NO HTTP types, cookie parsing, secret resolution,
//! provider selection, or Origin checks. Those concerns live in the
//! V1 delivery adapter (cookie extraction, `Set-Cookie` rendering) and
//! the bootstrap bridges (provider chain construction, JWT secret/env
//! wiring, plugin-API ↔ service-api field translation).
//!
//! The trait impl block lives here (a single `impl AuthService for
//! AuthApplicationService` block is required by Rust — see
//! E0119). The per-flow orchestration lives in [`login`] and
//! [`session`]; the trait methods are thin forwarders.

mod login;
mod session;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    AuthFlowReply, AuthProviderUrl, AuthRedirect, AuthUserInfo, AuthenticatedUserQuery,
    BuildLoginUrls, CompleteOAuthLogin, PresentedSession, AuthService, SessionRenewal,
};
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{
    OAuthProviderPort, OAuthSessionPort, PendingOAuthLoginPort,
};

/// Plain config-injected values for [`AuthApplicationService`].
///
/// Constructed by the composition root from the workspace `AuthConfig`
/// plus the V1 delivery adapter's redirect / callback configuration.
/// This crate never resolves secrets or inspects the environment to
/// build this struct.
#[derive(Debug, Clone)]
pub struct AuthApplicationServiceConfig {
    /// Chain's enabled provider subsequence, in the order `login_urls`
    /// MUST preserve in its reply. The application layer does not
    /// validate or filter this list against the port's `names` — the
    /// adapter / composition root is responsible for only ever
    /// enabling providers that exist in the chain.
    pub enabled_providers: Vec<String>,
    /// Configured pending-login flow namespace (spec §8.4). Bound into
    /// every `issue_batch` and `consume` call so a V1 callback cannot
    /// be replayed against a V2 pending-login batch (or vice versa).
    pub flow: String,
    /// Configured same-origin redirect URL for successful logins. The
    /// delivery adapter serves the Workbench at this URL; the
    /// application layer replies it as `AuthRedirect::location` after a
    /// successful `install_identity`.
    pub post_login_redirect: String,
}

/// Transport-agnostic implementation of
/// [`bcs_service_api::application::v1::auth::AuthService`].
pub struct AuthApplicationService {
    provider: Arc<dyn OAuthProviderPort>,
    session: Arc<dyn OAuthSessionPort>,
    pending: Arc<dyn PendingOAuthLoginPort>,
    config: AuthApplicationServiceConfig,
}

impl AuthApplicationService {
    pub fn new(
        provider: Arc<dyn OAuthProviderPort>,
        session: Arc<dyn OAuthSessionPort>,
        pending: Arc<dyn PendingOAuthLoginPort>,
        config: AuthApplicationServiceConfig,
    ) -> Self {
        Self {
            provider,
            session,
            pending,
            config,
        }
    }

    fn provider(&self) -> &Arc<dyn OAuthProviderPort> {
        &self.provider
    }

    fn session_port(&self) -> &Arc<dyn OAuthSessionPort> {
        &self.session
    }

    fn pending(&self) -> &Arc<dyn PendingOAuthLoginPort> {
        &self.pending
    }

    fn config(&self) -> &AuthApplicationServiceConfig {
        &self.config
    }
}

#[async_trait]
impl AuthService for AuthApplicationService {
    async fn login_urls(
        &self,
        command: BuildLoginUrls,
    ) -> AuthFlowReply<Vec<AuthProviderUrl>> {
        login::login_urls(self, command).await
    }

    async fn complete_login(&self, command: CompleteOAuthLogin) -> AuthFlowReply<AuthRedirect> {
        login::complete_login(self, command).await
    }

    async fn refresh_session(
        &self,
        command: PresentedSession,
    ) -> AuthFlowReply<SessionRenewal> {
        session::refresh_session(self, command).await
    }

    async fn logout(&self, command: Option<PresentedSession>) -> AuthFlowReply<()> {
        session::logout(self, command).await
    }

    async fn current_user(
        &self,
        query: AuthenticatedUserQuery,
    ) -> Result<AuthUserInfo, ApplicationError> {
        session::current_user(self, query).await
    }
}
