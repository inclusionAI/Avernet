use async_trait::async_trait;
use serde::Serialize;

use crate::application::RequestAuthHeaders;

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

#[derive(Debug, Clone)]
pub struct CompleteOAuthLogin {
    pub provider: String,
    pub code: Option<String>,
    pub auth_code: Option<String>,
    pub state: String,
    pub callback_base_url: String,
}

#[derive(Debug, Clone)]
pub struct ReadCurrentUser {
    pub headers: RequestAuthHeaders,
}

#[derive(Debug, Clone)]
pub struct RefreshSession {
    pub headers: RequestAuthHeaders,
}

#[derive(Debug, Clone)]
pub struct LogoutSession {
    pub headers: RequestAuthHeaders,
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

#[async_trait]
pub trait AuthService: Send + Sync {
    async fn login_urls(
        &self,
        request: BuildLoginUrls,
    ) -> Result<AuthProviderUrlList, ApplicationError>;

    async fn complete_login(
        &self,
        request: CompleteOAuthLogin,
    ) -> Result<AuthRedirect, ApplicationError>;

    async fn current_user(&self, request: ReadCurrentUser)
        -> Result<AuthUserInfo, ApplicationError>;

    async fn refresh_session(&self, request: RefreshSession)
        -> Result<SessionRenewal, ApplicationError>;

    async fn logout(&self, request: LogoutSession) -> Result<LogoutResult, ApplicationError>;
}
