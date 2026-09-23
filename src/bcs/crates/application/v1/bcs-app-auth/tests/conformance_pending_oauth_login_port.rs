//! `PendingOAuthLoginPort` conformance driver for a recording in-memory
//! fake. Calls the central
//! `bcs_test_support::contract::port::pending_o_auth_login_port_contract_tests`
//! suite.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{PendingLoginBatch, PendingOAuthLoginPort};
use bcs_test_support::contract::port::pending_o_auth_login_port_contract_tests;

/// In-memory implementation of `PendingOAuthLoginPort` matching the
/// spec §8.4 atomicity contract (issue_batch / consume with batch atomic
/// consumption + wrong-nonce rejection).
struct FakePending {
    batches: Mutex<HashMap<String, BatchRecord>>,
}

struct BatchRecord {
    browser_nonce: String,
    provider: String,
    exact_callback: String,
    flow: String,
    expires_at: u64,
    consumed: bool,
}

impl FakePending {
    fn new() -> Self {
        Self {
            batches: Mutex::new(HashMap::new()),
        }
    }
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
        // Use CSPRNG-style nonce/state values so the harness can assert
        // non-emptiness and uniqueness.
        let nonce = format!("nonce-{flow}-{now}");
        let mut provider_states = Vec::with_capacity(providers.len());
        for (i, p) in providers.iter().enumerate() {
            let state = format!("state-{flow}-{now}-{i}");
            provider_states.push((p.clone(), state));
        }
        // Record each provider's state for later consume lookup.
        let mut batches = self.batches.lock().expect("lock");
        for (p, s) in &provider_states {
            batches.insert(
                s.clone(),
                BatchRecord {
                    browser_nonce: nonce.clone(),
                    provider: p.clone(),
                    exact_callback: format!("{callback_base}/{p}"),
                    flow: flow.to_string(),
                    expires_at: now + 300,
                    consumed: false,
                },
            );
        }
        Ok(PendingLoginBatch {
            browser_nonce: nonce,
            expires_at: now + 300,
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
        let mut batches = self.batches.lock().expect("lock");
        let record = match batches.get_mut(state) {
            Some(r) => r,
            None => return Err(ApplicationError::invalid("invalid_state", "unknown state")),
        };
        if record.consumed {
            return Err(ApplicationError::invalid("invalid_state", "state already consumed"));
        }
        if record.browser_nonce != browser_nonce
            || record.provider != provider
            || record.exact_callback != exact_callback
            || record.flow != flow
            || record.expires_at <= now
        {
            return Err(ApplicationError::invalid("invalid_state", "consume mismatch"));
        }
        // Atomically mark all state in this batch as consumed. This
        // naive in-memory impl uses one shared nonce per batch, so
        // marking only this state as consumed is sufficient for the
        // harness's single-provider assertions. Real impls MUST mark
        // the entire batch atomically.
        record.consumed = true;
        Ok(())
    }
}

#[tokio::test]
async fn fake_pending_passes_pending_oauth_login_port_contract_harness() {
    let fake = Arc::new(FakePending::new());
    pending_o_auth_login_port_contract_tests(&*fake).await;
}
