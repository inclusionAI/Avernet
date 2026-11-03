//! Versioned secure OAuth application orchestration for the BCN V1 API
//! (V1 API auth plugin chain, Task 10).
//!
//! [`AuthApplicationService`] implements the transport-neutral
//! [`AuthService`] use-case contract: browser-bound login URL
//! issuance, the binding-complete callback order (validate → consume →
//! exchange → install), the Human projection, and session refresh and
//! logout. Flow outcomes are [`AuthFlowReply`]s — cookie instructions on
//! BOTH success and failure — and this crate never touches an HTTP type,
//! parses a cookie or Header, checks an Origin, or picks a provider
//! implementation: those stay in delivery, bootstrap, and configuration.
//!
//! Crate boundary (see also `CONTEXT.md` and `Cargo.toml`): production
//! code depends only on `bcs-service-api` application/port contracts
//! plus `async-trait`, `time`, and `tracing`. All provider, session, and
//! pending-login behavior arrives through the three injected
//! `port::oauth` ports; no adapter or concrete plugin is ever linked
//! here.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::error::ApplicationError;
use bcs_service_api::application::v1::{
    AuthRedirectLocation, AuthenticatedUserQuery, AuthFlowReply, AuthProviderUrlList,
    AuthUserInfo, BuildLoginUrls, CompleteOAuthLogin, LogoutData, AuthService,
    SessionRenewalData,
};
use bcs_service_api::port::oauth::{
    OAuthProviderPort, OAuthSessionPort, PendingOAuthLoginPort,
};

use crate::login::{complete_login_flow, login_urls_flow};
use crate::session::{current_user_flow, logout_flow, refresh_session_flow};

mod login;
mod session;

/// Transport-neutral secure OAuth orchestration (the shared `AuthService`
/// implementation both HTTP entries inject).
///
/// Everything injectable is injected: the three application ports, the
/// configured same-origin success redirect, and the configured login
/// flow namespace (the V1 entry uses `"v1"`, the legacy entry keeps its
/// own). The clock comes from `time` (unix seconds).
pub struct AuthApplicationService {
    providers: Arc<dyn OAuthProviderPort>,
    sessions: Arc<dyn OAuthSessionPort>,
    pending: Arc<dyn PendingOAuthLoginPort>,
    success_redirect_path: String,
    flow: String,
}

impl AuthApplicationService {
    pub fn new(
        providers: Arc<dyn OAuthProviderPort>,
        sessions: Arc<dyn OAuthSessionPort>,
        pending: Arc<dyn PendingOAuthLoginPort>,
        success_redirect_path: String,
        flow: String,
    ) -> Self {
        Self {
            providers,
            sessions,
            pending,
            success_redirect_path,
            flow,
        }
    }

    /// The configured same-origin redirect handed back on login success.
    pub fn success_redirect_path(&self) -> &str {
        &self.success_redirect_path
    }

    /// The configured flow namespace pairing issue/consume calls.
    pub fn flow(&self) -> &str {
        &self.flow
    }
}

#[async_trait]
impl AuthService for AuthApplicationService {
    async fn login_urls(
        &self,
        request: BuildLoginUrls,
    ) -> AuthFlowReply<AuthProviderUrlList> {
        login_urls_flow(self, request).await
    }

    async fn complete_login(
        &self,
        command: CompleteOAuthLogin,
    ) -> AuthFlowReply<AuthRedirectLocation> {
        complete_login_flow(self, command).await
    }

    async fn current_user(
        &self,
        query: AuthenticatedUserQuery,
    ) -> Result<AuthUserInfo, ApplicationError> {
        current_user_flow(self, query).await
    }

    async fn refresh_session(
        &self,
        session: bcs_service_api::application::v1::PresentedSession,
    ) -> AuthFlowReply<SessionRenewalData> {
        refresh_session_flow(self, session).await
    }

    async fn logout(
        &self,
        session: Option<bcs_service_api::application::v1::PresentedSession>,
    ) -> AuthFlowReply<LogoutData> {
        logout_flow(self, session).await
    }
}
