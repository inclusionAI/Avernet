use bcs_auth_api::OAuthProvider;
use bcs_auth_google::{GoogleOAuthConfig, GoogleOAuthProvider};
use axum::http::HeaderValue;

fn test_config() -> GoogleOAuthConfig {
    GoogleOAuthConfig {
        client_id: "test-client-id.apps.googleusercontent.com".to_string(),
        client_secret: "test-client-secret".to_string(),
    }
}

#[test]
fn google_provider_auth_url_contains_required_params() {
    let provider = GoogleOAuthProvider::new(test_config());
    let url = provider.auth_url("random-state-123", "http://localhost:21000/auth/callback/google");
    assert!(url.contains("accounts.google.com"));
    assert!(url.contains("client_id=test-client-id"));
    assert!(url.contains("state=random-state-123"));
    assert!(url.contains("redirect_uri="));
    assert!(url.contains("scope="));
}

#[test]
fn google_provider_name() {
    let provider = GoogleOAuthProvider::new(test_config());
    assert_eq!(provider.name(), "google");
}

/// Rule 25: the Google provider satisfies the shared offline `OAuthProvider`
/// contract that the mock and every other provider also pass.
#[test]
fn google_provider_passes_offline_contract() {
    let provider = GoogleOAuthProvider::new(test_config());
    bcs_test_support::run_oauth_provider_offline_contract(&provider);
}

#[test]
fn google_plugin_can_authenticate_with_cookie() {
    // Cookie detection is provider-agnostic (extract_session_cookie); the
    // session plugin lives in bcs-auth-api now, so test the shared helper.
    let mut headers = axum::http::HeaderMap::new();
    // No cookie → cannot authenticate
    assert!(!bcs_auth_api::extract_session_cookie(&headers).is_some());

    headers.insert("cookie", HeaderValue::from_static("bcs_session=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0Iiwic3JjIjoiZ29vZ2xlIiwiaWF0IjoxMDAwLCJleHAiOjIwMDB9.fake"));
    assert!(bcs_auth_api::extract_session_cookie(&headers).is_some());
}
