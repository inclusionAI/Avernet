//! WeChat OAuth endpoint constants and configuration.

/// WeChat QR connect authorization endpoint (for website login).
pub const WECHAT_AUTH_URL: &str = "https://open.weixin.qq.com/connect/qrconnect";

/// WeChat OAuth token exchange endpoint.
pub const WECHAT_TOKEN_URL: &str = "https://api.weixin.qq.com/sns/oauth2/access_token";

/// WeChat userinfo endpoint.
pub const WECHAT_USERINFO_URL: &str = "https://api.weixin.qq.com/sns/userinfo";

/// WeChat OAuth scope for website login.
pub const WECHAT_SCOPES: &str = "snsapi_login";

/// WeChat OAuth client configuration.
pub struct WeChatConfig {
    /// WeChat open-platform AppID (mapped from `client_id` in config).
    pub appid: String,
    /// WeChat open-platform AppSecret (mapped from `client_secret` in config).
    pub secret: String,
}