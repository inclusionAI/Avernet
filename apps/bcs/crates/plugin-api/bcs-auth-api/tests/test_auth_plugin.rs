//! Contract tests for `bcs-auth-api` types that need no concrete plugin impls.
//!
//! Chain-resolution tests (which require Local/Static/Noop plugins) live in
//! `bcs-auth-local/tests/chain_integration.rs`.

use bcs_auth_api::{AuthPluginChain, AuthPrincipal, AuthSource, OAuthError, OAuthToken, ProviderUserInfo};

/// Contract: Empty chain returns Ok(AuthResult { principal: None }) (anonymous).
#[tokio::test]
async fn empty_chain_returns_none() {
    let chain = AuthPluginChain::new(vec![]);
    let result = chain.authenticate(&axum::http::HeaderMap::new()).await;
    assert!(result.is_ok(), "Empty chain should return Ok");
    assert!(
        result.unwrap().principal.is_none(),
        "Empty chain should return principal = None"
    );
}

/// Contract: AuthPrincipal Default includes all optional fields as None.
#[test]
fn auth_principal_default_is_empty() {
    let principal = AuthPrincipal::default();
    assert!(principal.user_id.is_none());
    assert!(principal.user_name.is_none());
    assert!(principal.bot_uuid.is_none());
    assert!(principal.owner_id.is_none());
    assert!(principal.source_name.is_none());
}

/// Contract: OAuth variant name() returns the provider string.
#[test]
fn oauth_source_name_returns_provider() {
    let src = AuthSource::OAuth("google".to_string());
    assert_eq!(src.name(), "google");
    let src2 = AuthSource::OAuth("github".to_string());
    assert_eq!(src2.name(), "github");
}

/// Contract: AuthPrincipal::new with OAuth source carries provider name.
#[test]
fn oauth_principal_carries_source() {
    let src = AuthSource::OAuth("google".to_string());
    let p = AuthPrincipal::new(src);
    assert_eq!(p.source_name.as_deref(), Some("google"));
}

// ---------------------------------------------------------------------------
// OAuth types contract tests
// ---------------------------------------------------------------------------

/// Contract: OAuthToken deserializes from a typical provider JSON response.
#[test]
fn oauth_token_from_json() {
    let json = r#"{"access_token":"ya29.a0","token_type":"Bearer","expires_in":3599}"#;
    let token: OAuthToken = serde_json::from_str(json).expect("deserialize OAuthToken");
    assert_eq!(token.access_token, "ya29.a0");
    assert_eq!(token.token_type.as_deref(), Some("Bearer"));
    assert_eq!(token.expires_in, Some(3599));
    assert!(token.refresh_token.is_none());
}

/// Contract: OAuthToken with all optional fields present.
#[test]
fn oauth_token_with_refresh_token() {
    let json = r#"{"access_token":"ghu_123","token_type":"bearer","expires_in":28800,"refresh_token":"ghr_abc"}"#;
    let token: OAuthToken = serde_json::from_str(json).expect("deserialize OAuthToken");
    assert_eq!(token.access_token, "ghu_123");
    assert_eq!(token.refresh_token.as_deref(), Some("ghr_abc"));
}

/// Contract: ProviderUserInfo carries the expected fields.
#[test]
fn provider_user_info_minimal() {
    let info = ProviderUserInfo {
        id: "123456789".to_string(),
        name: Some("Alice".to_string()),
        email: None,
        avatar: None,
    };
    assert_eq!(info.id, "123456789");
    assert_eq!(info.name.as_deref(), Some("Alice"));
    assert!(info.email.is_none());
}

/// Contract: OAuthError Display contains the detail message.
#[test]
fn oauth_error_display() {
    let e = OAuthError::TokenExchangeFailed("timeout".to_string());
    assert!(e.to_string().contains("timeout"));

    let e2 = OAuthError::InvalidState("csrf mismatch".to_string());
    assert!(e2.to_string().contains("csrf mismatch"));

    let e3 = OAuthError::ProviderNotFound("google".to_string());
    assert!(e3.to_string().contains("google"));
}
