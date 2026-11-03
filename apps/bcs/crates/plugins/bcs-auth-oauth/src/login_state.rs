//! In-memory browser-bound pending OAuth login store (V1 API auth plugin
//! chain, Task 9).
//!
//! [`MemoryPendingOAuthLoginStore`] implements the
//! [`bcs_auth_api::login_state::PendingOAuthLoginStore`] contract for
//! single-instance deployments (plan §8.4 deployment clause: this period
//! ships a memory store; multi-instance deployments must use a shared,
//! atomically-consumable store or a verified fixed routing before
//! enabling OAuth — never a stateless fallback).
//!
//! Binding semantics (plan §8.4, non-negotiable here):
//! - the nonce and every state are independent OS-CSPRNG draws (uuid v4);
//!   they differ in length and value, so neither can equal or encode the
//!   other;
//! - pending records store ONLY the SHA-256 hex nonce hash, provider,
//!   exact callback, flow, and batch id — the raw nonce and state live
//!   solely in the returned [`LoginBatch`];
//! - matching AND whole-batch invalidation happen under ONE mutex, so
//!   concurrent consume of one batch has exactly one winner;
//! - any failed match (wrong browser, provider, callback, flow, TTL)
//!   consumes NOTHING — a wrong-browser callback never burns the
//!   initiating browser's batch.

use std::collections::{BTreeSet, HashMap};
use std::sync::Mutex;

use async_trait::async_trait;
use bcs_auth_api::login_state::{
    LoginBatch, LoginStateError, PendingOAuthLoginStore, PENDING_LOGIN_TTL_SECS,
};
use bcs_jwt::token_hash;
use subtle::ConstantTimeEq;

// Bound retained states across all batches, not just the number of browsers.
const MAX_PENDING_STATES: usize = 4096;
// Bound expiry-index work per operation; no whole-store scan under the mutex.
const EXPIRED_BATCH_CLEANUP_LIMIT: usize = 64;

struct PendingBatch {
    states: Vec<String>,
    expires_at: u64,
}

/// Internal pending record. Deliberately has no `Debug`: it holds the
/// nonce hash, and a derived impl would invite future field leaks.
struct PendingRecord {
    batch_id: String,
    /// SHA-256 hex of the browser nonce — the raw nonce is never stored.
    nonce_hash: String,
    provider: String,
    /// Exact per-provider callback `{callback_base}/{provider}`.
    callback: String,
    flow: String,
    expires_at: u64,
}

#[derive(Default)]
struct PendingInner {
    /// state -> record.
    records: HashMap<String, PendingRecord>,
    /// batch id -> every state of that batch (for whole-batch removal).
    batches: HashMap<String, PendingBatch>,
    /// One entry per live batch; removed on expiry AND successful consume.
    expirations: BTreeSet<(u64, String)>,
}

/// Single-process pending-login store over one mutex.
pub struct MemoryPendingOAuthLoginStore {
    inner: Mutex<PendingInner>,
}

impl Default for MemoryPendingOAuthLoginStore {
    fn default() -> Self {
        Self::new()
    }
}

impl MemoryPendingOAuthLoginStore {
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(PendingInner::default()),
        }
    }

    /// Remove every state of `batch_id` plus the batch entry. Whole-batch
    /// removal is what turns one successful consume into the batch's
    /// death (no other provider state of the same batch can complete);
    /// it is also the fail-closed cleanup for expired batches.
    fn consume_batch(inner: &mut PendingInner, batch_id: &str) {
        if let Some(batch) = inner.batches.remove(batch_id) {
            inner.expirations.remove(&(batch.expires_at, batch_id.to_string()));
            for state in batch.states {
                inner.records.remove(&state);
            }
        }
    }

    /// Reclaim only expired batches, in expiry order (also correct if the
    /// injected clock moves backwards). Live batches are never evicted.
    fn purge_expired(inner: &mut PendingInner, now: u64) {
        for _ in 0..EXPIRED_BATCH_CLEANUP_LIMIT {
            let Some((expires_at, batch_id)) = inner.expirations.first() else {
                break;
            };
            if *expires_at > now {
                break;
            }
            let batch_id = batch_id.clone();
            Self::consume_batch(inner, &batch_id);
        }
    }
}

#[async_trait]
impl PendingOAuthLoginStore for MemoryPendingOAuthLoginStore {
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<LoginBatch, LoginStateError> {
        if providers.is_empty() {
            // No enabled providers means no login batch at all — rejected
            // as invalid input, never an empty usable batch.
            return Err(LoginStateError::Invalid);
        }
        let expires_at = now
            .checked_add(PENDING_LOGIN_TTL_SECS)
            .ok_or(LoginStateError::Unavailable)?;
        let mut inner = lock_unpoisoned(&self.inner);
        Self::purge_expired(&mut inner, now);
        if providers.len() > MAX_PENDING_STATES - inner.records.len() {
            // Reject the entire batch before allocating or inserting any
            // credentials. Existing live logins retain their binding.
            return Err(LoginStateError::Unavailable);
        }
        let mut batch_id = new_batch_id();
        while inner.batches.contains_key(&batch_id) {
            batch_id = new_batch_id();
        }
        let nonce = new_browser_nonce();
        // Defensive CS-space guard: regenerate any state that collides
        // with an outstanding record (probability ~0 for OS-CSPRNG hex,
        // but a collision must never silently alias another batch).
        let mut provider_states = Vec::with_capacity(providers.len());
        for provider in providers {
            let mut state = new_state();
            while inner.records.contains_key(&state) {
                state = new_state();
            }
            let callback = format!("{callback_base}/{provider}");
            inner.records.insert(
                state.clone(),
                PendingRecord {
                    batch_id: batch_id.clone(),
                    nonce_hash: token_hash(&nonce),
                    provider: provider.clone(),
                    callback,
                    flow: flow.to_string(),
                    expires_at,
                },
            );
            provider_states.push((provider.clone(), state));
        }
        inner.expirations.insert((expires_at, batch_id.clone()));
        inner.batches.insert(
            batch_id,
            PendingBatch {
                states: provider_states.iter().map(|(_, state)| state.clone()).collect(),
                expires_at,
            },
        );
        drop(inner);

        Ok(LoginBatch {
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
    ) -> Result<(), LoginStateError> {
        // The presented nonce is always hashed to 64 hex chars, so the
        // comparison below runs over equal-length digests only.
        let presented_hash = token_hash(browser_nonce);
        let mut inner = lock_unpoisoned(&self.inner);
        Self::purge_expired(&mut inner, now);

        // Decide under the shared borrow first, mutate second: matching
        // and whole-batch invalidation still complete within this single
        // lock hold (there is no await between them).
        let Some((matched, batch_id)) = inner.records.get(state).map(|record| {
            let matched = now < record.expires_at
                && record.flow == flow
                && record.provider == provider
                && record.callback == exact_callback
                && constant_time_eq(record.nonce_hash.as_bytes(), presented_hash.as_bytes());
            (matched, record.batch_id.clone())
        }) else {
            return Err(LoginStateError::Invalid);
        };
        if !matched {
            // Expired records beyond this call's cleanup budget remain in
            // the expiry index for the next sweep; they never authenticate.
            // A wrong browser/provider/callback/flow leaves live state intact.
            return Err(LoginStateError::Invalid);
        }
        Self::consume_batch(&mut inner, &batch_id);
        Ok(())
    }
}

/// Constant-time compare of the stored and presented nonce digests. Both
/// are SHA-256 hex (length is public), but the comparison itself must
/// never leak how many leading bytes matched.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    bool::from(a.ct_eq(b))
}

/// Lock a std mutex, recovering from poisoning: panicking while holding
/// the login-state lock never invalidates the surviving records.
fn lock_unpoisoned(inner: &Mutex<PendingInner>) -> std::sync::MutexGuard<'_, PendingInner> {
    match inner.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

/// Random batch id from the OS CSPRNG (uuid v4, dash-less lowercase hex).
fn new_batch_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// Browser nonce: one OS-CSPRNG uuid v4 (122 bits), 32 hex chars. The raw
/// value is handed out only inside the returned `LoginBatch` so the
/// delivery layer can write the temporary challenge cookie; the store
/// persists its SHA-256 only.
fn new_browser_nonce() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// One-time state: 64 hex chars (two OS-CSPRNG uuid v4 draws). The
/// different length from the nonce makes equality/derivation impossible,
/// and it stays unguessable before the store record is observable.
fn new_state() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    )
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::unwrap_used)]

    use super::*;

    const CALLBACK_BASE: &str = "https://bcs.example.com/openapi/v1/auth/callback";

    fn store() -> MemoryPendingOAuthLoginStore {
        MemoryPendingOAuthLoginStore::new()
    }

    /// The pending record must persist ONLY the nonce hash: the stored
    /// digest differs from the raw nonce and is the 64-char SHA-256 hex
    /// of it.
    #[tokio::test]
    async fn pending_record_stores_nonce_hash_not_raw_nonce() {
        let store = store();
        let batch = store
            .issue_batch(&["github".into()], CALLBACK_BASE, "v1", 100)
            .await
            .expect("issue must succeed");
        let inner = store.inner.lock().unwrap();
        assert_eq!(inner.records.len(), 1);
        let record = inner.records.values().next().unwrap();
        assert_ne!(record.nonce_hash, batch.browser_nonce);
        assert_eq!(record.nonce_hash.len(), 64);
        assert_eq!(record.nonce_hash, token_hash(&batch.browser_nonce));
    }

    /// Nonce and states are independent CSPRNG draws of different length:
    /// they can never be equal or contain one another.
    #[tokio::test]
    async fn nonce_and_state_are_independent_csprng_draws() {
        let store = store();
        let batch = store
            .issue_batch(&["github".into(), "google".into()], CALLBACK_BASE, "v1", 100)
            .await
            .expect("issue must succeed");
        assert_eq!(batch.browser_nonce.len(), 32);
        for (provider, state) in &batch.provider_states {
            assert_eq!(state.len(), 64);
            assert!(state != &batch.browser_nonce, "provider {provider}");
            assert!(!state.contains(&batch.browser_nonce));
            assert!(!batch.browser_nonce.contains(state.as_str()));
        }
    }

    /// The exact callback is the base plus the provider path segment.
    #[tokio::test]
    async fn exact_callback_is_base_plus_provider_segment() {
        let store = store();
        let batch = store
            .issue_batch(&["github".into()], CALLBACK_BASE, "v1", 100)
            .await
            .expect("issue must succeed");
        let state = batch.provider_states[0].1.clone();
        store
            .consume(
                &state,
                &batch.browser_nonce,
                "github",
                "https://bcs.example.com/openapi/v1/auth/callback/github",
                "v1",
                101,
            )
            .await
            .expect("exact callback must match");
    }
}

#[cfg(test)]
#[path = "login_state/cleanup_tests.rs"]
mod cleanup_tests;
