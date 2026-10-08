//! In-memory `PendingOAuthLoginStore` for tests and single-instance dev.
//!
//! Implements [`bcs_auth_api::PendingOAuthLoginStore`] with a
//! `Mutex<HashMap<String, PendingRecord>>`. The same mutex covers the
//! match-check AND the batch consumption — the atomicity required by
//! spec §8.4 "完成登录".
//!
//! # Production constraints
//!
//! A memory store implies a single auth instance or verified fixed
//! routing (spec §8.4 + §8.6). Multi-instance without fixed routing
//! MUST NOT enable OAuth until a DB-backed shared pending-login store
//! is available (Task 15 gate). No-failure-fallback is forbidden: a
//! missing state row MUST fail the consume rather than auto-issue a
//! fresh state.
//!
//! # Hash function
//!
//! Nonce hash uses SHA-256 — the same primitive as `bcs_jwt::token_hash`
//! for the JWT fingerprint stored in the identity session table. The
//! nonce is NOT a JWT, but the hash primitive is the same; the memory
//! store calls SHA-256 directly (rather than `bcs_jwt::token_hash`) so
//! the dependency relationship stays honest — nonces do not flow
//! through the JWT crate.
//!
//! # Constant-time nonce hash compare
//!
//! The memory store compares nonce hashes with a manual constant-time
//! loop over the fixed 32-byte SHA-256 digest — no `subtle` crate
//! dependency (the workspace has none). The compare is fixed-length
//! (both sides are SHA-256 digests) and short-circuits only on length
//! mismatch (which never happens here). The loop walks all 32 bytes
//! unconditionally and accumulates the XOR fold into a single byte.

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use bcs_auth_api::{LoginBatch, LoginStateError, PendingOAuthLoginStore, PENDING_LOGIN_TTL_SECS};

/// In-memory pending-login state store.
///
/// Each `new()` returns a fresh store backed by an owned
/// `Mutex<HashMap<String, PendingRecord>>`. Use one instance for the
/// `bcs-auth-oauth` consumer in production (single-instance / fixed
/// routing only — see the module docs and spec §8.4).
#[derive(Default)]
pub struct MemoryPendingOAuthLoginStore {
    records: Mutex<HashMap<String, PendingRecord>>,
}

#[derive(Clone)]
struct PendingRecord {
    /// SHA-256 digest of the browser nonce (32 raw bytes). Never the
    /// raw nonce; only its hash is persisted so a store dump cannot
    /// yield usable nonces.
    nonce_hash: [u8; 32],
    /// Provider this state is bound to within its batch.
    provider: String,
    /// Exact per-provider callback URL:
    /// `format!("{callback_base}/{provider}")`.
    exact_callback: String,
    /// Flow namespace — `"v1"` or `"legacy"` per spec §8.4.
    flow: String,
    /// Absolute Unix-seconds expiry. `now + PENDING_LOGIN_TTL_SECS`
    /// at issue; consume rejects when `now > expires_at`.
    expires_at: u64,
    /// Batch identity shared by all provider states issued by the
    /// same `issue_batch` call. All records with the same `batch_id`
    /// are removed atomically when one record fully matches the
    /// consume payload.
    batch_id: [u8; 16],
}

impl MemoryPendingOAuthLoginStore {
    /// Construct an empty pending-login store.
    pub fn new() -> Self {
        Self::default()
    }
}

/// SHA-256(`nonce`) → 32 raw digest bytes.
///
/// The store persists ONLY this digest, never the raw nonce. The
/// comparison is done over the raw bytes by [`constant_time_eq`].
fn nonce_hash(nonce: &str) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(nonce.as_bytes());
    let digest = hasher.finalize();
    let mut out = [0u8; 32];
    out.copy_from_slice(&digest);
    out
}

/// Manual constant-time compare over two fixed-length 32-byte SHA-256
/// digests.
///
/// Operates on equal-length slices; the precondition of equal length
/// is satisfied because both sides are SHA-256 digests. The loop walks
/// all 32 bytes unconditionally and accumulates the XOR fold into a
/// single byte — no short-circuit on byte difference, no branch on
/// the compared value.
fn constant_time_eq(a: &[u8; 32], b: &[u8; 32]) -> bool {
    let mut acc: u8 = 0;
    for i in 0..32 {
        acc |= a[i] ^ b[i];
    }
    acc == 0
}

/// CSPRNG 32-hex-char browser nonce from uuid v4. Independent draw
/// from any per-provider state value — nonces are NOT derivable from
/// states and vice versa.
fn fresh_browser_nonce() -> String {
    Uuid::new_v4().simple().to_string()
}

/// CSPRNG per-provider state value (uuid v4 hex). Independent draw
/// from the nonce and from other providers' states.
fn fresh_state() -> String {
    Uuid::new_v4().simple().to_string()
}

/// Fresh batch identifier (uuid v4) — shared by all per-provider
/// records of one `issue_batch` so `consume` can atomically delete
/// every record in the batch on a full match.
fn fresh_batch_id() -> [u8; 16] {
    *Uuid::new_v4().as_bytes()
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
        let browser_nonce = fresh_browser_nonce();
        let nonce_hash = nonce_hash(&browser_nonce);
        let batch_id = fresh_batch_id();
        let expires_at = now.saturating_add(PENDING_LOGIN_TTL_SECS);

        let mut provider_states = Vec::with_capacity(providers.len());
        let mut records = self
            .records
            .lock()
            .expect("pending-login store lock not poisoned");

        for provider in providers {
            let state = fresh_state();
            let exact_callback = format!("{callback_base}/{provider}");
            let record = PendingRecord {
                nonce_hash,
                provider: provider.clone(),
                exact_callback,
                flow: flow.to_string(),
                expires_at,
                batch_id,
            };
            records.insert(state.clone(), record);
            provider_states.push((provider.clone(), state));
        }

        Ok(LoginBatch {
            browser_nonce,
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
        let mut records = self
            .records
            .lock()
            .expect("pending-login store lock not poisoned");

        // 1. State lookup + expiry. A missing state cannot consume any
        // batch — there is no record to match. An expired state likewise
        // fails its own consume alone; siblings remain valid.
        let record = match records.get(state) {
            Some(r) => r.clone(),
            None => return Err(LoginStateError::Invalid),
        };
        if now > record.expires_at {
            return Err(LoginStateError::Invalid);
        }

        // 2. Nonce hash (constant-time). Browser B with A's state and a
        // wrong nonce MUST NOT consume A's pending state — return
        // Invalid WITHOUT touching the batch.
        let presented = nonce_hash(browser_nonce);
        if !constant_time_eq(&presented, &record.nonce_hash) {
            return Err(LoginStateError::Invalid);
        }

        // 3. Provider / exact_callback / flow namespace binding. Each
        // state binds its provider; a mismatch fails without consuming
        // the batch.
        if record.provider != provider
            || record.exact_callback != exact_callback
            || record.flow != flow
        {
            return Err(LoginStateError::Invalid);
        }

        // 4. Full match → atomic batch consumption under the SAME lock
        // that performed the match-check above (spec §8.4 "原子消费").
        // A concurrent consume against any sibling state in this batch
        // observes an empty record and returns Invalid.
        let batch_id = record.batch_id;
        records.retain(|_, r| r.batch_id != batch_id);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Unit-level check: constant-time compare is reflexive and
    /// distinguishes even a single-byte difference.
    #[test]
    fn constant_time_compare_is_exact_on_fixed_length_digests() {
        let a = [0u8; 32];
        let b = [0u8; 32];
        assert!(constant_time_eq(&a, &b), "equal digests match");
        let mut c = [0u8; 32];
        c[15] = 1;
        assert!(!constant_time_eq(&a, &c), "single-bit diff fails");
    }

    /// Sanity: nonce_hash is stable for the same input and distinct
    /// for different inputs.
    #[test]
    fn nonce_hash_is_stable_and_distinct() {
        let a1 = nonce_hash("browser-a");
        let a2 = nonce_hash("browser-a");
        let b = nonce_hash("browser-b");
        assert_eq!(a1, a2, "same nonce → same hash");
        assert_ne!(a1, b, "different nonces → different hashes");
    }

    /// Sanity: `fresh_browser_nonce` and `fresh_state` produce
    /// distinct, non-empty values (smoke test of CSPRNG independence).
    #[test]
    fn fresh_nonces_and_states_are_distinct() {
        let n = fresh_browser_nonce();
        let s = fresh_state();
        assert!(!n.is_empty());
        assert!(!s.is_empty());
        assert_ne!(n, s, "nonce and state MUST be independent draws");
    }
}
