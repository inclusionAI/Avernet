//! Alipay OAuth endpoint constants and configuration.

/// Alipay OAuth authorization URL (for public-app website login).
pub const ALIPAY_AUTH_URL: &str = "https://openauth.alipay.com/oauth2/publicAppAuthorize.htm";

/// Alipay OpenAPI gateway endpoint.
pub const ALIPAY_GATEWAY_URL: &str = "https://openapi.alipay.com/gateway.do";

/// Alipay OAuth scope for user info access.
pub const ALIPAY_SCOPES: &str = "auth_user";

/// Alipay API charset.
pub const ALIPAY_CHARSET: &str = "utf-8";

/// Alipay API version.
pub const ALIPAY_API_VERSION: &str = "1.0";

/// Alipay signing algorithm.
pub const ALIPAY_SIGN_TYPE: &str = "RSA2";

/// Alipay success response code.
pub const ALIPAY_SUCCESS_CODE: &str = "10000";

/// Alipay OAuth client configuration.
pub struct AlipayConfig {
    /// Alipay application APPID (mapped from `client_id` in config).
    pub app_id: String,
    /// Application RSA private key in PEM format, for signing requests.
    pub private_key_pem: String,
    /// Alipay RSA public key in PEM format, for verifying gateway responses.
    pub alipay_public_key_pem: String,
}