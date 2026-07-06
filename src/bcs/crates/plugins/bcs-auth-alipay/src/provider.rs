//! Alipay OAuth provider implementation.

use async_trait::async_trait;
use bcs_auth_api::{OAuthError, OAuthProvider, OAuthToken, ProviderUserInfo};
use std::collections::BTreeMap;
use tracing::{info, warn};

use crate::config::{
    AlipayConfig, ALIPAY_API_VERSION, ALIPAY_AUTH_URL, ALIPAY_CHARSET, ALIPAY_GATEWAY_URL,
    ALIPAY_SCOPES, ALIPAY_SIGN_TYPE, ALIPAY_SUCCESS_CODE,
};
use crate::sign::{parse_private_key, parse_public_key, sign_params, AlipaySignError};

/// Alipay OAuth token response (nested under `alipay_system_oauth_token_response`).
#[derive(Debug, serde::Deserialize)]
struct AlipayTokenData {
    access_token: Option<String>,
    expires_in: Option<u64>,
    refresh_token: Option<String>,
    open_id: Option<String>,
    user_id: Option<String>,
    code: Option<String>,
    sub_code: Option<String>,
    msg: Option<String>,
    sub_msg: Option<String>,
}

/// Top-level gateway response for `alipay.system.oauth.token`.
#[derive(Debug, serde::Deserialize)]
struct AlipayTokenResponse {
    #[serde(rename = "alipay_system_oauth_token_response")]
    data: Option<AlipayTokenData>,
    sign: Option<String>,
}

/// Alipay userinfo response data (nested under `alipay_user_info_share_response`).
#[derive(Debug, serde::Deserialize)]
struct AlipayUserInfoData {
    open_id: Option<String>,
    nick_name: Option<String>,
    avatar: Option<String>,
    code: Option<String>,
    sub_code: Option<String>,
    msg: Option<String>,
    sub_msg: Option<String>,
}

/// Top-level gateway response for `alipay.user.info.share`.
#[derive(Debug, serde::Deserialize)]
struct AlipayUserInfoResponse {
    #[serde(rename = "alipay_user_info_share_response")]
    data: Option<AlipayUserInfoData>,
    sign: Option<String>,
}

/// Check an Alipay response code and return an error message if not success.
fn alipay_error_message(
    code: Option<&str>,
    sub_code: Option<&str>,
    msg: Option<&str>,
    sub_msg: Option<&str>,
) -> Option<String> {
    match code {
        Some(c) if c == ALIPAY_SUCCESS_CODE => None,
        Some(c) => {
            let detail = match (sub_code, sub_msg) {
                (Some(sc), Some(sm)) => format!(" {sc}: {sm}"),
                (Some(sc), None) => format!(" {sc}"),
                _ => String::new(),
            };
            let msg_part = msg.unwrap_or("unknown error");
            Some(format!("Alipay error code={c} ({msg_part}{detail})"))
        }
        None => Some("Alipay response missing code field".to_string()),
    }
}

/// Alipay OAuth provider.
pub struct AlipayOAuthProvider {
    app_id: String,
    private_key: rsa::RsaPrivateKey,
    alipay_public_key: rsa::RsaPublicKey,
    http: reqwest::Client,
}

impl AlipayOAuthProvider {
    /// Create a new Alipay provider. Returns an error if the PEM keys cannot be
    /// parsed — this provides fail-fast validation at startup.
    pub fn new(config: AlipayConfig) -> Result<Self, AlipaySignError> {
        let private_key = parse_private_key(&config.private_key_pem)?;
        let alipay_public_key = parse_public_key(&config.alipay_public_key_pem)?;
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .unwrap_or_default();
        Ok(Self {
            app_id: config.app_id,
            private_key,
            alipay_public_key,
            http,
        })
    }

    /// Build the common gateway parameters for an Alipay API call.
    fn common_params(&self, method: &str) -> BTreeMap<String, String> {
        let mut params = BTreeMap::new();
        params.insert("app_id".to_string(), self.app_id.clone());
        params.insert("method".to_string(), method.to_string());
        params.insert("charset".to_string(), ALIPAY_CHARSET.to_string());
        params.insert("sign_type".to_string(), ALIPAY_SIGN_TYPE.to_string());
        params.insert(
            "timestamp".to_string(),
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        );
        params.insert("version".to_string(), ALIPAY_API_VERSION.to_string());
        params
    }

    /// Sign the parameters and add the `sign` field. The caller provides the
    /// appropriate error constructor (TokenExchangeFailed vs UserInfoFailed).
    fn sign_and_append(
        &self,
        params: &mut BTreeMap<String, String>,
        make_err: fn(String) -> OAuthError,
    ) -> Result<(), OAuthError> {
        let signature = sign_params(params, &self.private_key)
            .map_err(|e| make_err(format!("Alipay signing error: {e}")))?;
        params.insert("sign".to_string(), signature);
        Ok(())
    }

    /// Verify the response signature from the raw JSON body. Alipay signs the
    /// raw JSON text of the `{method}_response` value (not reconstructed params).
    /// Failures are logged as warnings but do not block the request (fail-open).
    fn verify_response_sign_from_body(&self, body: &str, response_key: &str, sign_value: &str) {
        // Extract the raw JSON content between `"response_key":` and `,"sign":`
        // Alipay signs exactly this substring.
        let start_pattern = format!("\"{}\":", response_key);
        let start = match body.find(&start_pattern) {
            Some(idx) => idx + start_pattern.len(),
            None => {
                warn!("Alipay response verify: cannot find response key '{response_key}' in body");
                return;
            }
        };
        // Find the end: the last `}` before `,"sign"` or end of outer object
        let sign_marker = ",\"sign\"";
        let end = body[start..].find(sign_marker)
            .map(|idx| start + idx)
            .unwrap_or(body.len() - 1);
        let content = &body[start..end];

        match crate::sign::verify_raw(content.as_bytes(), sign_value, &self.alipay_public_key) {
            Ok(()) => {}
            Err(e) => {
                warn!(error = %e, "Alipay response signature verification failed (not blocking)");
            }
        }
    }
}

#[async_trait]
impl OAuthProvider for AlipayOAuthProvider {
    fn name(&self) -> &str {
        "alipay"
    }

    fn auth_url(&self, state: &str, redirect_uri: &str) -> String {
        format!(
            "{}?app_id={}&scope={}&redirect_uri={}&state={}",
            ALIPAY_AUTH_URL,
            urlencoding::encode(&self.app_id),
            urlencoding::encode(ALIPAY_SCOPES),
            urlencoding::encode(redirect_uri),
            urlencoding::encode(state),
        )
    }

    async fn exchange_code(&self, code: &str, _redirect_uri: &str) -> Result<OAuthToken, OAuthError> {
        let mut params = self.common_params("alipay.system.oauth.token");
        params.insert("grant_type".to_string(), "authorization_code".to_string());
        params.insert("code".to_string(), code.to_string());

        self.sign_and_append(&mut params, OAuthError::TokenExchangeFailed)?;

        let resp = self
            .http
            .post(ALIPAY_GATEWAY_URL)
            .form(&params)
            .send()
            .await
            .map_err(|e| OAuthError::TokenExchangeFailed(e.to_string()))?;

        let body = resp
            .text()
            .await
            .map_err(|e| {
                OAuthError::TokenExchangeFailed(format!("read Alipay token response body: {e}"))
            })?;

        let gateway: AlipayTokenResponse = serde_json::from_str(&body)
            .map_err(|e| OAuthError::TokenExchangeFailed(format!("parse Alipay token response: {e}")))?;

        let data = gateway.data.ok_or_else(|| {
            OAuthError::TokenExchangeFailed(
                "Alipay token response missing alipay_system_oauth_token_response".to_string(),
            )
        })?;

        // Verify response signature if present (fail-open: log only).
        // Alipay signs the raw JSON of the response value, not reconstructed params.
        if let Some(sign) = &gateway.sign {
            self.verify_response_sign_from_body(
                &body,
                "alipay_system_oauth_token_response",
                sign,
            );
        }

        if let Some(err) = alipay_error_message(
            data.code.as_deref(),
            data.sub_code.as_deref(),
            data.msg.as_deref(),
            data.sub_msg.as_deref(),
        ) {
            return Err(OAuthError::TokenExchangeFailed(err));
        }

        let access_token = data.access_token.ok_or_else(|| {
            OAuthError::TokenExchangeFailed(
                "Alipay token response missing access_token".to_string(),
            )
        })?;

        let mut extra = std::collections::HashMap::new();
        if let Some(open_id) = data.open_id {
            extra.insert("open_id".to_string(), open_id);
        }
        if let Some(user_id) = data.user_id {
            extra.insert("user_id".to_string(), user_id);
        }

        Ok(OAuthToken {
            access_token,
            token_type: Some("bearer".to_string()),
            expires_in: data.expires_in,
            refresh_token: data.refresh_token,
            extra,
        })
    }

    async fn get_user_info(&self, token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError> {
        let mut params = self.common_params("alipay.user.info.share");
        params.insert("auth_token".to_string(), token.access_token.clone());

        self.sign_and_append(&mut params, OAuthError::UserInfoFailed)?;

        let resp = self
            .http
            .post(ALIPAY_GATEWAY_URL)
            .form(&params)
            .send()
            .await
            .map_err(|e| OAuthError::UserInfoFailed(e.to_string()))?;

        let body = resp
            .text()
            .await
            .map_err(|e| {
                OAuthError::UserInfoFailed(format!("read Alipay userinfo response body: {e}"))
            })?;

        let gateway: AlipayUserInfoResponse = serde_json::from_str(&body)
            .map_err(|e| OAuthError::UserInfoFailed(format!("parse Alipay userinfo response: {e}")))?;

        let data = gateway.data.ok_or_else(|| {
            OAuthError::UserInfoFailed(
                "Alipay userinfo response missing alipay_user_info_share_response".to_string(),
            )
        })?;

        // Verify response signature if present (fail-open: log only).
        if let Some(sign) = &gateway.sign {
            self.verify_response_sign_from_body(
                &body,
                "alipay_user_info_share_response",
                sign,
            );
        }

        if let Some(err) = alipay_error_message(
            data.code.as_deref(),
            data.sub_code.as_deref(),
            data.msg.as_deref(),
            data.sub_msg.as_deref(),
        ) {
            return Err(OAuthError::UserInfoFailed(err));
        }

        let alipay_open_id = data.open_id.ok_or_else(|| {
            OAuthError::UserInfoFailed("Alipay userinfo response missing open_id".to_string())
        })?;

        info!(open_id = %alipay_open_id, nick_name = ?data.nick_name, "Alipay userinfo retrieved");

        Ok(ProviderUserInfo {
            id: alipay_open_id,
            name: data.nick_name,
            email: None,
            avatar: data.avatar,
        })
    }
}