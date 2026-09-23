//! `OAuthProviderPort` conformance driver for the recording `FakeProvider`
//! used by the V1 app-auth integration tests. Calls the central
//! `bcs_test_support::contract::port::o_auth_provider_port_contract_tests`
//! harness with the sample inputs the fake is pre-programmed to
//! recognize.

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{ExternalLoginIdentity, OAuthProviderPort};
use bcs_test_support::contract::port::o_auth_provider_port_contract_tests;

struct FakeProvider {
    names: Vec<String>,
    auth_url_calls: Mutex<Vec<(String, String, String)>>,
    exchange_calls: Mutex<Vec<(String, String, String)>>,
}

impl FakeProvider {
    fn new(names: Vec<String>) -> Self {
        Self {
            names,
            auth_url_calls: Mutex::new(Vec::new()),
            exchange_calls: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl OAuthProviderPort for FakeProvider {
    async fn names(&self) -> Vec<String> {
        self.names.clone()
    }

    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        self.auth_url_calls.lock().expect("lock").push((
            provider.to_string(),
            state.to_string(),
            redirect_uri.to_string(),
        ));
        Ok(format!(
            "https://provider.example/{provider}?state={state}&redirect_uri={redirect_uri}"
        ))
    }

    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        self.exchange_calls.lock().expect("lock").push((
            provider.to_string(),
            code.to_string(),
            redirect_uri.to_string(),
        ));
        Ok(ExternalLoginIdentity {
            provider: provider.to_string(),
            external_user_id: format!("ext-{provider}-{code}"),
            name: Some("Contract Test User".to_string()),
            avatar: None,
        })
    }
}

#[tokio::test]
async fn fake_provider_passes_oauth_provider_port_contract_harness() {
    let fake = Arc::new(FakeProvider::new(vec!["github".to_string()]));
    o_auth_provider_port_contract_tests(
        &*fake,
        "github",
        "state-123",
        "https://bcs.example.com/openapi/v1/auth/callback/github",
        "code-456",
    )
    .await;

    // Recording a non-empty call set proves the harness actually
    // exercised the trait's methods, not a sparse subset.
    assert!(
        !fake.auth_url_calls.lock().expect("lock").is_empty(),
        "harness MUST call auth_url"
    );
    assert!(
        !fake.exchange_calls.lock().expect("lock").is_empty(),
        "harness MUST call exchange_user"
    );
}
