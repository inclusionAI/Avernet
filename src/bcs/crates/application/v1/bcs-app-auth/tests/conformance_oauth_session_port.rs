//! `OAuthSessionPort` conformance driver for a recording in-memory fake.
//! Calls the central
//! `bcs_test_support::contract::port::o_auth_session_port_contract_tests`
//! suite.

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{BrowserSession, ExternalLoginIdentity, OAuthSessionPort};
use bcs_test_support::contract::port::o_auth_session_port_contract_tests;

struct FakeSession {
    install_calls: Mutex<Vec<ExternalLoginIdentity>>,
    refresh_calls: Mutex<Vec<String>>,
    revoke_calls: Mutex<Vec<String>>,
}

impl FakeSession {
    fn new() -> Self {
        Self {
            install_calls: Mutex::new(Vec::new()),
            refresh_calls: Mutex::new(Vec::new()),
            revoke_calls: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl OAuthSessionPort for FakeSession {
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        self.install_calls.lock().expect("lock").push(identity);
        Ok(BrowserSession {
            token: format!("install-token-{}", now),
            expires_at: now + 3600,
        })
    }

    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, ApplicationError> {
        self.refresh_calls.lock().expect("lock").push(token.to_string());
        Ok(BrowserSession {
            token: format!("refreshed-{}-{}", token, now),
            expires_at: now + 7200,
        })
    }

    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        self.revoke_calls.lock().expect("lock").push(token.to_string());
        // Both store outcomes Revoked / NotCurrent are idempotent Ok at
        // this port layer per spec §8.5.
        Ok(())
    }
}

#[tokio::test]
async fn fake_session_passes_oauth_session_port_contract_harness() {
    let fake = Arc::new(FakeSession::new());
    o_auth_session_port_contract_tests(&*fake).await;

    // Prove the harness exercised all three lifecycle methods.
    assert!(
        !fake.install_calls.lock().expect("lock").is_empty(),
        "harness MUST call install_identity"
    );
    assert!(
        !fake.refresh_calls.lock().expect("lock").is_empty(),
        "harness MUST call refresh"
    );
    // The harness calls revoke twice for the idempotency invariant.
    assert_eq!(
        fake.revoke_calls.lock().expect("lock").len(),
        2,
        "harness MUST call revoke twice to verify idempotency"
    );
}
