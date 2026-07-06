//! Rule 21 isolation double for the `OAuthProvider` plugin boundary.
//!
//! `MockOAuthProvider` is a deterministic, network-free `OAuthProvider` so the
//! `/auth/*` routes and the OAuth login flow can be exercised in tests without
//! reaching real Google/GitHub HTTP endpoints. It can also be told to fail a
//! given stage to cover error paths.

use std::collections::HashMap;

use async_trait::async_trait;

use bcs_auth_api::{OAuthError, OAuthProvider, OAuthToken, ProviderUserInfo};

/// Which stage (if any) the mock should fail, for error-path coverage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum MockFailure {
    /// Succeed at every stage (default).
    #[default]
    None,
    /// `exchange_code` returns `OAuthError::TokenExchangeFailed`.
    Exchange,
    /// `get_user_info` returns `OAuthError::UserInfoFailed`.
    UserInfo,
}

/// Deterministic `OAuthProvider` test double.
///
/// `exchange_code` echoes the input code into a synthetic access token;
/// `get_user_info` returns the configured `ProviderUserInfo`. Both are pure and
/// offline.
#[derive(Debug, Clone)]
pub struct MockOAuthProvider {
    name: String,
    user: ProviderUserInfo,
    fail: MockFailure,
}

impl MockOAuthProvider {
    /// A provider named `name` that resolves every login to user `user_id`.
    pub fn new(name: impl Into<String>, user_id: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            user: ProviderUserInfo {
                id: user_id.into(),
                name: Some("Mock User".to_string()),
                email: None,
                avatar: None,
            },
            fail: MockFailure::None,
        }
    }

    /// Override the full `ProviderUserInfo` returned by `get_user_info`.
    pub fn with_user(mut self, user: ProviderUserInfo) -> Self {
        self.user = user;
        self
    }

    /// Force a given stage to fail (error-path tests).
    pub fn with_failure(mut self, fail: MockFailure) -> Self {
        self.fail = fail;
        self
    }
}

#[async_trait]
impl OAuthProvider for MockOAuthProvider {
    fn name(&self) -> &str {
        &self.name
    }

    fn auth_url(&self, state: &str, redirect_uri: &str) -> String {
        // Mirror the real providers' query shape so contract assertions on
        // state / redirect_uri / client_id hold for the mock too.
        format!(
            "https://mock.oauth.local/authorize?client_id=mock-client&redirect_uri={}&response_type=code&state={}",
            redirect_uri, state
        )
    }

    async fn exchange_code(
        &self,
        code: &str,
        _redirect_uri: &str,
    ) -> Result<OAuthToken, OAuthError> {
        if self.fail == MockFailure::Exchange {
            return Err(OAuthError::TokenExchangeFailed("mock forced failure".into()));
        }
        Ok(OAuthToken {
            access_token: format!("mock-access-for-{code}"),
            token_type: Some("bearer".to_string()),
            expires_in: Some(3600),
            refresh_token: None,
            extra: HashMap::new(),
        })
    }

    async fn get_user_info(&self, _token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError> {
        if self.fail == MockFailure::UserInfo {
            return Err(OAuthError::UserInfoFailed("mock forced failure".into()));
        }
        Ok(self.user.clone())
    }
}

/// Rule 25 conformance suite for the `OAuthProvider` contract (offline part).
///
/// Every `OAuthProvider` implementation — google, github, the mock, and any
/// future provider — must pass this. It asserts the network-free,
/// provider-agnostic guarantees the `/auth/*` routes rely on:
/// - a stable, non-empty `name`
/// - an `auth_url` that carries the CSRF `state` and the `redirect_uri`
///   (raw or percent-encoded) and targets an `https` authorize endpoint
///
/// The IO half of the contract (`exchange_code` → `get_user_info`) calls real
/// provider HTTP endpoints, so it cannot be run against the live providers in
/// CI. It is covered for the network-free `MockOAuthProvider` by
/// [`run_oauth_provider_roundtrip_contract`], and per-provider field mapping is
/// covered by each provider's own unit tests.
pub fn run_oauth_provider_offline_contract<P: OAuthProvider + ?Sized>(provider: &P) {
    assert!(!provider.name().is_empty(), "provider name must be non-empty");

    let state = "csrf-state-token-xyz";
    let redirect_uri = "https://app.example.com/auth/callback/x";
    let url = provider.auth_url(state, redirect_uri);
    assert!(
        url.starts_with("https://"),
        "auth_url must target an https endpoint, got: {url}"
    );
    assert!(
        url.contains(state),
        "auth_url must carry the CSRF state, got: {url}"
    );
    assert!(
        url.contains(redirect_uri) || url.contains(&urlencoding_encode(redirect_uri)),
        "auth_url must carry the redirect_uri (raw or percent-encoded), got: {url}"
    );
}

/// IO half of the `OAuthProvider` contract: `exchange_code` → `get_user_info`
/// yields a non-empty external `id`. Only runnable against network-free
/// implementations such as [`MockOAuthProvider`]; live providers verify their
/// IO behavior through their own integration tests.
pub async fn run_oauth_provider_roundtrip_contract<P: OAuthProvider + ?Sized>(provider: &P) {
    let redirect_uri = "https://app.example.com/auth/callback/x";
    let token = provider
        .exchange_code("contract-test-code", redirect_uri)
        .await
        .expect("exchange_code should succeed in the happy path");
    let info = provider
        .get_user_info(&token)
        .await
        .expect("get_user_info should succeed in the happy path");
    assert!(
        !info.id.is_empty(),
        "provider must return a non-empty external user id"
    );
}

/// Minimal percent-encoding for the contract's redirect_uri assertion, so the
/// suite does not pull in the `urlencoding` crate. Encodes the characters the
/// real providers encode in a redirect URI (`:` and `/`).
fn urlencoding_encode(s: &str) -> String {
    s.replace(':', "%3A").replace('/', "%2F")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn mock_passes_provider_contract() {
        let p = MockOAuthProvider::new("mock", "contract-user");
        run_oauth_provider_offline_contract(&p);
        run_oauth_provider_roundtrip_contract(&p).await;
    }

    #[tokio::test]
    async fn mock_exchange_and_userinfo_succeed() {
        let p = MockOAuthProvider::new("mock", "user-1");
        assert_eq!(p.name(), "mock");
        let token = p.exchange_code("the-code", "https://app/cb").await.unwrap();
        assert_eq!(token.access_token, "mock-access-for-the-code");
        let info = p.get_user_info(&token).await.unwrap();
        assert_eq!(info.id, "user-1");
    }

    #[tokio::test]
    async fn mock_forced_failures() {
        let ex = MockOAuthProvider::new("mock", "u").with_failure(MockFailure::Exchange);
        assert!(ex.exchange_code("c", "r").await.is_err());

        let ui = MockOAuthProvider::new("mock", "u").with_failure(MockFailure::UserInfo);
        let tok = ui.exchange_code("c", "r").await.unwrap();
        assert!(ui.get_user_info(&tok).await.is_err());
    }
}
