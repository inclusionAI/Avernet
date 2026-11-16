//! Browser-bound pending OAuth login batches (V1 API auth plugin chain,
//! Task 9).
//!
//! Plan §8.4: every OAuth login flow first issues ONE batch of provider
//! login URLs bound to the INITIATING browser. The batch pairs a fresh
//! browser nonce (delivered via the temporary login-challenge cookie)
//! with one random state per provider; the two values are independent
//! OS-CSPRNG draws — never equal, never derivable from each other.
//!
//! The pending-state record keeps ONLY the SHA-256 nonce hash, the
//! provider, the exact callback, the flow namespace, and a
//! [`PENDING_LOGIN_TTL_SECS`] expiry — the raw nonce is never stored.
//!
//! Consume is an atomic match-and-invalidate: state, nonce hash,
//! provider, exact callback, flow, and TTL must ALL match before the
//! WHOLE batch (every provider's state) is invalidated under one lock.
//! A mismatch consumes nothing — in particular a wrong-browser callback
//! never burns the initiating browser's pending state. Storage
//! failures are `Unavailable` and yield no usable `LoginBatch`.
//!
//! Redaction (plan §8.4 credential-log red line): [`LoginBatch`] carries
//! the raw nonce and states, so it has a hand-written `Debug` that shows
//! only the expiry and provider count. Never add derived payload
//! formatting here; Task 10/11 log-capture tests assert this.

use async_trait::async_trait;

/// Pending-state lifetime in seconds (5 minutes): the browser cookie and
/// the server-side pending record expire together, after which any
/// callback is rejected and the record is cleaned up.
pub const PENDING_LOGIN_TTL_SECS: u64 = 300;

/// One issued login batch for a single initiating browser.
///
/// `browser_nonce` is the value the delivery layer writes into the
/// host-only temporary login-challenge cookie; `provider_states` pairs
/// each requested provider with its one-time state. All states in one
/// batch share one fate: the first successful consume invalidates the
/// whole batch.
#[derive(Clone)]
pub struct LoginBatch {
    pub browser_nonce: String,
    pub expires_at: u64,
    pub provider_states: Vec<(String, String)>,
}

impl std::fmt::Debug for LoginBatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LoginBatch")
            .field("expires_at", &self.expires_at)
            .field("provider_count", &self.provider_states.len())
            .finish_non_exhaustive()
    }
}

/// Closed error taxonomy of the pending-login store.
///
/// `Invalid` is every rejected match (unknown/expired/rebound state,
/// wrong browser/provider/callback/flow); `Unavailable` is a store
/// failure (including exhausted bounded storage capacity). An issue failure
/// surfacing `Unavailable` means NO usable login URLs were produced. Neither
/// variant ever carries credentials.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum LoginStateError {
    #[error("pending login state rejected")]
    Invalid,
    #[error("pending login store unavailable")]
    Unavailable,
}

/// Pending one-time-consume OAuth login state, bound to the initiating
/// browser (plan §8.4).
///
/// Implementations MUST:
/// - draw nonce and state from the OS CSPRNG, independently (the nonce is
///   never equal to or derivable from any state);
/// - persist only the nonce HASH plus provider/exact-callback/flow/TTL;
/// - verify the nonce hash with a constant-time comparison;
/// - invalidate the ENTIRE batch on any single successful consume, with
///   matching and invalidation performed under one lock;
/// - leave the pending state UNconsumed on any failed match, so a
///   wrong-browser callback cannot burn browser A's batch;
/// - propagate store failures as `Unavailable` — never answer an issue
///   failure with a usable [`LoginBatch`].
#[async_trait]
pub trait PendingOAuthLoginStore: Send + Sync {
    /// Issue a fresh batch for the given providers under one flow
    /// namespace. `callback_base` is the public base callback path; the
    /// exact per-provider callback is `{callback_base}/{provider}`.
    /// A capacity failure returns `Unavailable` without a partial batch or
    /// eviction of other unexpired batches.
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<LoginBatch, LoginStateError>;

    /// Atomically match-and-consume the pending record for `state`.
    /// Every input must match (nonce hash, provider, exact callback,
    /// flow, TTL) or nothing is consumed and `Invalid` is returned.
    async fn consume(
        &self,
        state: &str,
        browser_nonce: &str,
        provider: &str,
        exact_callback: &str,
        flow: &str,
        now: u64,
    ) -> Result<(), LoginStateError>;
}
