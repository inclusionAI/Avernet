//! WeChat OAuth provider implementation.

use async_trait::async_trait;
use bcs_auth_api::{OAuthError, OAuthProvider, OAuthToken, ProviderUserInfo};
use serde::Deserialize;
use tracing::{info, warn};

use crate::config::{WeChatConfig, WECHAT_AUTH_URL, WECHAT_SCOPES, WECHAT_TOKEN_URL, WECHAT_USERINFO_URL};

/// WeChat token response. WeChat returns HTTP 200 even on error; errors are
/// indicated by a non-zero `errcode` field.
#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct WeChatTokenResponse {
    access_token: Option<String>,
    expires_in: Option<u64>,
    refresh_token: Option<String>,
    openid: Option<String>,
    scope: Option<String>,
    unionid: Option<String>,
    errcode: Option<i64>,
    errmsg: Option<String>,
}

/// WeChat userinfo response.
#[derive(Debug, Deserialize)]
struct WeChatUserInfoResponse {
    openid: Option<String>,
    nickname: Option<String>,
    headimgurl: Option<String>,
    /// Error code — present and non-zero on failure.
    errcode: Option<i64>,
    errmsg: Option<String>,
}

/// WeChat OAuth provider.
pub struct WeChatOAuthProvider {
    appid: String,
    secret: String,
    http: reqwest::Client,
}

impl WeChatOAuthProvider {
    pub fn new(config: WeChatConfig) -> Self {
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .unwrap_or_default();
        Self {
            appid: config.appid,
            secret: config.secret,
            http,
        }
    }
}

#[async_trait]
impl OAuthProvider for WeChatOAuthProvider {
    fn name(&self) -> &str {
        "wechat"
    }

    fn auth_url(&self, state: &str, redirect_uri: &str) -> String {
        format!(
            "{}?appid={}&redirect_uri={}&response_type=code&scope={}&state={}#wechat_redirect",
            WECHAT_AUTH_URL,
            urlencoding::encode(&self.appid),
            urlencoding::encode(redirect_uri),
            urlencoding::encode(WECHAT_SCOPES),
            urlencoding::encode(state),
        )
    }

    async fn exchange_code(&self, code: &str, _redirect_uri: &str) -> Result<OAuthToken, OAuthError> {
        // WeChat uses GET (not POST) for token exchange, with credentials as query params.
        // Note: WeChat does NOT accept redirect_uri in the token request.
        let url = format!(
            "{}?appid={}&secret={}&code={}&grant_type=authorization_code",
            WECHAT_TOKEN_URL,
            urlencoding::encode(&self.appid),
            urlencoding::encode(&self.secret),
            urlencoding::encode(code),
        );

        let resp = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| OAuthError::TokenExchangeFailed(e.without_url().to_string()))?;

        let body = resp
            .text()
            .await
            .map_err(|e| OAuthError::TokenExchangeFailed(format!("read token response body: {}", e.without_url())))?;

        let wechat_token: WeChatTokenResponse = serde_json::from_str(&body)
            .map_err(|e| OAuthError::TokenExchangeFailed(format!("parse token response: {e}")))?;

        // WeChat returns HTTP 200 even on error; check errcode.
        if let Some(errcode) = wechat_token.errcode {
            if errcode != 0 {
                let msg = wechat_token.errmsg.unwrap_or_default();
                warn!(errcode, %msg, "WeChat token exchange failed");
                return Err(OAuthError::TokenExchangeFailed(format!(
                    "WeChat errcode={errcode}: {msg}"
                )));
            }
        }

        let access_token = wechat_token.access_token.ok_or_else(|| {
            OAuthError::TokenExchangeFailed("WeChat token response missing access_token".to_string())
        })?;

        let mut extra = std::collections::HashMap::new();
        if let Some(openid) = wechat_token.openid {
            extra.insert("openid".to_string(), openid);
        }
        if let Some(unionid) = wechat_token.unionid {
            extra.insert("unionid".to_string(), unionid);
        }

        Ok(OAuthToken {
            access_token,
            token_type: Some("bearer".to_string()),
            expires_in: wechat_token.expires_in,
            refresh_token: wechat_token.refresh_token,
            extra,
        })
    }

    async fn get_user_info(&self, token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError> {
        let openid = token.extra.get("openid").ok_or_else(|| {
            OAuthError::UserInfoFailed("WeChat token missing openid in extra".to_string())
        })?;

        // WeChat uses access_token as a query param (not Bearer header).
        let url = format!(
            "{}?access_token={}&openid={}&lang=zh_CN",
            WECHAT_USERINFO_URL,
            urlencoding::encode(&token.access_token),
            urlencoding::encode(openid),
        );

        let resp = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| OAuthError::UserInfoFailed(e.without_url().to_string()))?;

        let body = resp
            .text()
            .await
            .map_err(|e| OAuthError::UserInfoFailed(format!("read userinfo response body: {}", e.without_url())))?;

        let user: WeChatUserInfoResponse = serde_json::from_str(&body)
            .map_err(|e| OAuthError::UserInfoFailed(format!("parse userinfo response: {e}")))?;

        if let Some(errcode) = user.errcode {
            if errcode != 0 {
                let msg = user.errmsg.unwrap_or_default();
                warn!(errcode, %msg, "WeChat userinfo request failed");
                return Err(OAuthError::UserInfoFailed(format!(
                    "WeChat errcode={errcode}: {msg}"
                )));
            }
        }

        let wechat_openid = user.openid.ok_or_else(|| {
            OAuthError::UserInfoFailed("WeChat userinfo response missing openid".to_string())
        })?;

        info!(openid = %wechat_openid, nickname = ?user.nickname, "WeChat userinfo retrieved");

        Ok(ProviderUserInfo {
            id: wechat_openid,
            name: user.nickname.or_else(|| Some("微信用户".to_string())),
            email: None,
            avatar: user.headimgurl,
        })
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn oauth_request_errors_strip_credential_bearing_urls() {
        let source = include_str!("provider.rs");
        let production = source.split("#[cfg(test)]").next().expect("production source");
        assert!(!production.contains(concat!("TokenExchangeFailed(e.", "to_string())")));
        assert!(!production.contains(concat!("UserInfoFailed(e.", "to_string())")));
        assert_eq!(production.matches("without_url()").count(), 4);
    }
}
