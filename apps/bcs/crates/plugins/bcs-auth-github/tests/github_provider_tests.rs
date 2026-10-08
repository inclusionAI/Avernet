//! Unit tests for the GitHub OAuth provider.

use bcs_auth_api::OAuthProvider;
use bcs_auth_github::{GitHubOAuthConfig, GitHubOAuthProvider};

#[test]
fn github_provider_name() {
    let config = GitHubOAuthConfig {
        client_id: "test-id".to_string(),
        client_secret: "test-secret".to_string(),
    };
    let provider = GitHubOAuthProvider::new(config);
    assert_eq!(provider.name(), "github");
}

#[test]
fn github_auth_url_format() {
    let config = GitHubOAuthConfig {
        client_id: "my-github-client-id".to_string(),
        client_secret: "secret".to_string(),
    };
    let provider = GitHubOAuthProvider::new(config);
    let url = provider.auth_url("csrf-state-abc", "http://localhost:21000/auth/callback/github");

    assert!(url.starts_with("https://github.com/login/oauth/authorize"));
    assert!(url.contains("client_id=my-github-client-id"));
    assert!(url.contains("state=csrf-state-abc"));
    assert!(url.contains("redirect_uri="));
    assert!(url.contains("scope="));
}

/// Rule 25: the GitHub provider satisfies the shared offline `OAuthProvider`
/// contract that the mock and every other provider also pass.
#[test]
fn github_provider_passes_offline_contract() {
    let provider = GitHubOAuthProvider::new(GitHubOAuthConfig {
        client_id: "gh-client".to_string(),
        client_secret: "secret".to_string(),
    });
    bcs_test_support::run_oauth_provider_offline_contract(&provider);
}