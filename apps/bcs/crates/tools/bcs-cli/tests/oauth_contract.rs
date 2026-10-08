//! Conformance test for the bcs-cli `oauth` provider extension point (Rule 25).
//!
//! This integration test is its own binary, so its `inventory::submit!` is
//! scoped to this binary only (it does NOT leak into the lib unit-test binary,
//! which asserts the no-provider stub fallback).
//!
//! It locks the PUBLIC contract that an internal overlay provider must honor:
//!   - compiled-in credential defaults flow through `default_oauth_client_*`,
//!   - `try_get_oauth_headers` delegates success (returns the provider headers).
//! The failure/auth_url path is covered in `oauth_contract_failure.rs`.

use std::collections::HashMap;

use async_trait::async_trait;

use bcs_cli::oauth::{
    self, AuthRequiredCallback, OAuthError, OAuthHeaderProvider, OAuthHeaderProviderRegistration,
};

struct FakeSuccessProvider;

#[async_trait]
impl OAuthHeaderProvider for FakeSuccessProvider {
    fn default_client_id(&self) -> &'static str {
        "fake-id"
    }
    fn default_client_secret(&self) -> &'static str {
        "fake-secret"
    }

    async fn get_headers(
        &self,
        _client_id: String,
        _client_secret: String,
        _on_auth_required: Option<AuthRequiredCallback>,
    ) -> Result<HashMap<String, String>, OAuthError> {
        let mut headers = HashMap::new();
        headers.insert("Authorization".to_string(), "Bearer fake-cached-token".to_string());
        Ok(headers)
    }
}

static FAKE: FakeSuccessProvider = FakeSuccessProvider;

inventory::submit! {
    OAuthHeaderProviderRegistration { provider: &FAKE }
}

#[tokio::test]
async fn credential_defaults_come_from_registered_provider() {
    // A linked provider must drive the compiled-in defaults seen by main's
    // resolve_oauth_credentials() (env override still wins; default = provider).
    assert_eq!(oauth::default_oauth_client_id(), "fake-id");
    assert_eq!(oauth::default_oauth_client_secret(), "fake-secret");
}

#[tokio::test]
async fn success_headers_are_delegated_to_registered_provider() {
    let headers = oauth::try_get_oauth_headers("ignored".to_string(), "ignored".to_string(), None)
        .await
        .expect("registered provider must be used, not the stub");
    assert_eq!(
        headers.get("Authorization").map(String::as_str),
        Some("Bearer fake-cached-token"),
    );
}
