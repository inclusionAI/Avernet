//! GitHub OAuth provider implementation.

use async_trait::async_trait;
use bcs_auth_api::{OAuthError, OAuthProvider, OAuthToken, ProviderUserInfo};
use serde::Deserialize;
use tracing::{info, warn};

use crate::config::{GitHubOAuthConfig, GITHUB_AUTH_URL, GITHUB_SCOPES, GITHUB_TOKEN_URL, GITHUB_USERINFO_URL};

/// GitHub-specific token response.
#[derive(Debug, Deserialize)]
struct GitHubTokenResponse {
    access_token: String,
    #[serde(default, rename = "token_type")]
    token_type: Option<String>,
    #[serde(default, rename = "expires_in")]
    expires_in: Option<u64>,
    #[serde(default, rename = "refresh_token")]
    refresh_token: Option<String>,
}

/// GitHub API `/user` response.
#[derive(Debug, Deserialize)]
struct GitHubUserInfoResponse {
    /// GitHub user ID (integer, deserialized from JSON number).
    id: i64,
    /// Display name — may be null for users who haven't set one.
    name: Option<String>,
    /// Username (login). Used as fallback when `name` is null.
    login: String,
    /// Avatar URL.
    avatar_url: Option<String>,
    /// Email (only set for public email addresses).
    email: Option<String>,
}

/// GitHub OAuth provider.
pub struct GitHubOAuthProvider {
    client_id: String,
    client_secret: String,
    http: reqwest::Client,
}

impl GitHubOAuthProvider {
    pub fn new(config: GitHubOAuthConfig) -> Self {
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            // api.github.com rejects requests with no User-Agent (HTTP 403),
            // so a UA is mandatory for the GitHub provider.
            .user_agent(concat!("bcs/", env!("CARGO_PKG_VERSION")))
            .build()
            .unwrap_or_default();
        Self {
            client_id: config.client_id,
            client_secret: config.client_secret,
            http,
        }
    }
}

#[async_trait]
impl OAuthProvider for GitHubOAuthProvider {
    fn name(&self) -> &str {
        "github"
    }

    fn auth_url(&self, state: &str, redirect_uri: &str) -> String {
        format!(
            "{}?client_id={}&redirect_uri={}&response_type=code&scope={}&state={}",
            GITHUB_AUTH_URL,
            urlencoding::encode(&self.client_id),
            urlencoding::encode(redirect_uri),
            urlencoding::encode(GITHUB_SCOPES),
            urlencoding::encode(state),
        )
    }

    async fn exchange_code(&self, code: &str, redirect_uri: &str) -> Result<OAuthToken, OAuthError> {
        let resp = self
            .http
            .post(GITHUB_TOKEN_URL)
            // GitHub defaults to form-encoded; request JSON explicitly.
            .header("Accept", "application/json")
            .form(&[
                ("code", code.to_string()),
                ("client_id", self.client_id.clone()),
                ("client_secret", self.client_secret.clone()),
                ("redirect_uri", redirect_uri.to_string()),
                ("grant_type", "authorization_code".to_string()),
            ])
            .send()
            .await
            .map_err(|e| OAuthError::TokenExchangeFailed(e.to_string()))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            warn!(%status, %body, "GitHub token exchange failed");
            return Err(OAuthError::TokenExchangeFailed(format!(
                "token endpoint returned {}: {}",
                status, body
            )));
        }

        let gh_token: GitHubTokenResponse = resp
            .json()
            .await
            .map_err(|e| OAuthError::TokenExchangeFailed(format!("parse token response: {e}")))?;

        Ok(OAuthToken {
            access_token: gh_token.access_token,
            token_type: gh_token.token_type,
            expires_in: gh_token.expires_in,
            refresh_token: gh_token.refresh_token,
            extra: std::collections::HashMap::new(),
        })
    }

    async fn get_user_info(&self, token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError> {
        let resp = self
            .http
            .get(GITHUB_USERINFO_URL)
            .bearer_auth(&token.access_token)
            .send()
            .await
            .map_err(|e| OAuthError::UserInfoFailed(e.to_string()))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            warn!(%status, %body, "GitHub userinfo request failed");
            return Err(OAuthError::UserInfoFailed(format!(
                "userinfo endpoint returned {}: {}",
                status, body
            )));
        }

        let user: GitHubUserInfoResponse = resp
            .json()
            .await
            .map_err(|e| OAuthError::UserInfoFailed(format!("parse userinfo response: {e}")))?;

        info!(gh_id = user.id, name = ?user.name, "GitHub userinfo retrieved");

        // GitHub `name` can be null; fall back to `login`.
        let display_name = user.name.or_else(|| Some(user.login.clone()));

        Ok(ProviderUserInfo {
            id: user.id.to_string(),
            name: display_name,
            email: user.email,
            avatar: user.avatar_url,
        })
    }
}