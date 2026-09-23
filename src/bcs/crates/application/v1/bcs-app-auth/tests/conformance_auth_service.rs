//! `AuthService` conformance driver for the production
//! `AuthApplicationService`. Calls the central
//! `bcs_test_support::contract::application::auth_service_contract_tests`
//! harness against recording fakes for the three sub-ports
//! (`OAuthProviderPort`, `OAuthSessionPort`, `PendingOAuthLoginPort`),
//! exercising the V1 OpenAPI auth facade at the application layer.

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_auth::{AuthApplicationService, AuthApplicationServiceConfig};
use bcs_service_api::application::v1::auth::AuthService;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort,
    PendingLoginBatch, PendingOAuthLoginPort,
};
use bcs_test_support::contract::application::auth_service_contract_tests;

// ─────────────────────────── fakes (happy-path) ────────────────────────

#[derive(Default)]
struct FakePending {
    issue_calls: Mutex<Vec<(Vec<String>, String, String, u64)>>,
    consume_calls: Mutex<Vec<(String, String, String, String, String, u64)>>,
}

#[async_trait]
impl PendingOAuthLoginPort for FakePending {
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError> {
        let nonce = "harness-nonce".to_string();
        let expires_at = now + 300;
        let provider_states: Vec<(String, String)> = providers
            .iter()
            .map(|p| (p.clone(), format!("state-{p}")))
            .collect();
        self.issue_calls.lock().expect("lock").push((
            providers.to_vec(),
            callback_base.to_string(),
            flow.to_string(),
            now,
        ));
        Ok(PendingLoginBatch {
            browser_nonce: nonce,
            expires_at,
            provider_states,
        })
    }

    async fn consume(
        &self,
        state: &str,
        browser_nonce: &str,
        provider: &str,
        exact_callback: &str,
        flow: &str,
        now: u64,
    ) -> Result<(), ApplicationError> {
        self.consume_calls.lock().expect("lock").push((
            state.to_string(),
            browser_nonce.to_string(),
            provider.to_string(),
            exact_callback.to_string(),
            flow.to_string(),
            now,
        ));
        Ok(())
    }
}

#[derive(Default)]
struct FakeProvider {
    names: Vec<String>,
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
        Ok(format!(
            "https://provider.example/{provider}?state={state}&redirect_uri={redirect_uri}"
        ))
    }

    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        _redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        Ok(ExternalLoginIdentity {
            provider: provider.to_string(),
            external_user_id: format!("ext-{provider}-{code}"),
            name: Some("Conformance Harness".to_string()),
            avatar: None,
        })
    }
}

#[derive(Default)]
struct FakeSession {
    refresh_calls: Mutex<Vec<String>>,
    revoke_calls: Mutex<Vec<String>>,
}

#[async_trait]
impl OAuthSessionPort for FakeSession {
    async fn install_identity(
        &self,
        _identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        Ok(BrowserSession {
            token: format!("install-token-{now}"),
            expires_at: now + 3600,
        })
    }

    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, ApplicationError> {
        self.refresh_calls.lock().expect("lock").push(token.to_string());
        Ok(BrowserSession {
            token: format!("refresh-{token}-{now}"),
            expires_at: now + 7200,
        })
    }

    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        self.revoke_calls.lock().expect("lock").push(token.to_string());
        Ok(())
    }
}

// ─────────────────────────── test stack ────────────────────────────────

#[tokio::test]
async fn auth_application_service_passes_auth_service_contract_harness() {
    let pending = Arc::new(FakePending::default());
    let provider = Arc::new(FakeProvider {
        names: vec!["github".to_string()],
    });
    let session = Arc::new(FakeSession::default());
    let config = AuthApplicationServiceConfig {
        enabled_providers: vec!["github".to_string()],
        flow: "openapi.v1".to_string(),
        post_login_redirect: "https://host/workbench".to_string(),
    };
    let svc = AuthApplicationService::new(
        provider.clone() as Arc<dyn OAuthProviderPort>,
        session.clone() as Arc<dyn OAuthSessionPort>,
        pending as Arc<dyn PendingOAuthLoginPort>,
        config,
    );

    auth_service_contract_tests(
        &svc as &dyn AuthService,
        "https://host/openapi/v1/auth/callback",
        "github",
        "state-github",
        "harness-nonce",
        "code-123",
        "presented-token-456",
    )
    .await;

    // Prove the harness exercised all three lifecycle paths.
    let refresh_calls = session.refresh_calls.lock().unwrap().len();
    let revoke_calls = session.revoke_calls.lock().unwrap().len();
    assert!(refresh_calls >= 1, "harness MUST call refresh");
    assert!(revoke_calls >= 1, "harness MUST call revoke at least once");
}
