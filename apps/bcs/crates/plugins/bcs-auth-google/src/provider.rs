//! Google OAuth provider implementation.

use async_trait::async_trait;
use bcs_auth_api::{OAuthError, OAuthProvider, OAuthToken, ProviderUserInfo};
use serde::Deserialize;
use tracing::{info, warn};

use crate::config::{GoogleOAuthConfig, GOOGLE_AUTH_URL, GOOGLE_SCOPES, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL};

/// Google-specific token response (subset of fields we care about).
#[derive(Debug, Deserialize)]
struct GoogleTokenResponse {
    access_token: String,
    #[serde(default, rename = "token_type")]
    token_type: Option<String>,
    #[serde(default, rename = "expires_in")]
    expires_in: Option<u64>,
    #[serde(default, rename = "refresh_token")]
    refresh_token: Option<String>,
    #[serde(default, rename = "id_token")]
    id_token: Option<String>,
}

/// Google userinfo response.
#[derive(Debug, Deserialize)]
struct GoogleUserInfoResponse {
    sub: String,
    name: Option<String>,
    email: Option<String>,
    picture: Option<String>,
}

/// Google OAuth provider.
pub struct GoogleOAuthProvider {
    client_id: String,
    client_secret: String,
    http: reqwest::Client,
}

impl GoogleOAuthProvider {
    pub fn new(config: GoogleOAuthConfig) -> Self {
        // Bound outbound calls so a hung Google endpoint can't pin the callback
        // request indefinitely. Falls back to a default client if the builder
        // fails (it won't with only a timeout set).
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
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
impl OAuthProvider for GoogleOAuthProvider {
    fn name(&self) -> &str {
        "google"
    }

    fn auth_url(&self, state: &str, redirect_uri: &str) -> String {
        format!(
            "{}?client_id={}&redirect_uri={}&response_type=code&scope={}&state={}&access_type=offline&prompt=consent",
            GOOGLE_AUTH_URL,
            urlencoding::encode(&self.client_id),
            urlencoding::encode(redirect_uri),
            urlencoding::encode(GOOGLE_SCOPES),
            urlencoding::encode(state),
        )
    }

    async fn exchange_code(&self, code: &str, redirect_uri: &str) -> Result<OAuthToken, OAuthError> {
        let resp = self
            .http
            .post(GOOGLE_TOKEN_URL)
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
            warn!(%status, %body, "Google token exchange failed");
            return Err(OAuthError::TokenExchangeFailed(format!(
                "token endpoint returned {}: {}",
                status, body
            )));
        }

        let google_token: GoogleTokenResponse = resp
            .json()
            .await
            .map_err(|e| OAuthError::TokenExchangeFailed(format!("parse token response: {e}")))?;

        let mut extra = std::collections::HashMap::new();
        if let Some(id_token) = google_token.id_token {
            extra.insert("id_token".to_string(), id_token);
        }

        Ok(OAuthToken {
            access_token: google_token.access_token,
            token_type: google_token.token_type,
            expires_in: google_token.expires_in,
            refresh_token: google_token.refresh_token,
            extra,
        })
    }

    async fn get_user_info(&self, token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError> {
        let resp = self
            .http
            .get(GOOGLE_USERINFO_URL)
            .bearer_auth(&token.access_token)
            .send()
            .await
            .map_err(|e| OAuthError::UserInfoFailed(e.to_string()))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            warn!(%status, %body, "Google userinfo request failed");
            return Err(OAuthError::UserInfoFailed(format!(
                "userinfo endpoint returned {}: {}",
                status, body
            )));
        }

        let user: GoogleUserInfoResponse = resp
            .json()
            .await
            .map_err(|e| OAuthError::UserInfoFailed(format!("parse userinfo response: {e}")))?;

        info!(sub = %user.sub, name = ?user.name, "Google userinfo retrieved");

        Ok(ProviderUserInfo {
            id: user.sub,
            name: user.name,
            email: user.email,
            avatar: user.picture,
        })
    }
}
