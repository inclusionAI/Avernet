//! OAuthProvider trait — each provider plugin implements this.

use async_trait::async_trait;

use crate::oauth_types::{OAuthError, OAuthToken, ProviderUserInfo};

/// Trait that each OAuth provider (Google, GitHub, etc.) implements.
///
/// The trait is object-safe so it can be used as `Arc<dyn OAuthProvider>`.
#[async_trait]
pub trait OAuthProvider: Send + Sync + 'static {
    /// Provider identifier (e.g. "google", "github").
    fn name(&self) -> &str;

    /// Build the authorization URL to redirect the user to.
    fn auth_url(&self, state: &str, redirect_uri: &str) -> String;

    /// Exchange an authorization code for tokens.
    async fn exchange_code(&self, code: &str, redirect_uri: &str)
        -> Result<OAuthToken, OAuthError>;

    /// Fetch normalized user info using the access token.
    async fn get_user_info(&self, token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError>;
}
