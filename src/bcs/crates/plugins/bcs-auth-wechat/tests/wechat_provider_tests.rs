//! Unit tests for the WeChat OAuth provider.

use bcs_auth_api::OAuthProvider;
use bcs_auth_wechat::{WeChatConfig, WeChatOAuthProvider};

fn test_config() -> WeChatConfig {
    WeChatConfig {
        appid: "wx1234567890abcdef".to_string(),
        secret: "test_wechat_app_secret".to_string(),
    }
}

#[test]
fn wechat_provider_name() {
    let provider = WeChatOAuthProvider::new(test_config());
    assert_eq!(provider.name(), "wechat");
}

#[test]
fn wechat_auth_url_format() {
    let provider = WeChatOAuthProvider::new(test_config());
    let url = provider.auth_url("csrf-state-abc", "http://localhost:21000/auth/callback/wechat");

    assert!(url.starts_with("https://open.weixin.qq.com/connect/qrconnect"));
    assert!(url.contains("appid=wx1234567890abcdef"));
    assert!(url.contains("state=csrf-state-abc"));
    assert!(url.contains("redirect_uri="));
    assert!(url.contains("scope=snsapi_login"));
    assert!(url.contains("response_type=code"));
    // WeChat requires the trailing fragment
    assert!(url.ends_with("#wechat_redirect"));
}

#[test]
fn wechat_auth_url_contains_state() {
    let provider = WeChatOAuthProvider::new(test_config());
    let url = provider.auth_url("my-state-123", "https://example.com/callback/wechat");
    assert!(url.contains("state=my-state-123"));
}

/// Rule 25: the WeChat provider satisfies the shared offline `OAuthProvider`
/// contract that the mock and every other provider also pass.
#[test]
fn wechat_provider_passes_offline_contract() {
    let provider = WeChatOAuthProvider::new(test_config());
    bcs_test_support::run_oauth_provider_offline_contract(&provider);
}