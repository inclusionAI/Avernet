//! Browser-bound OAuth login state — pending-login store contract.
//!
//! Spec §8.4 ("OAuth state 的浏览器绑定 / 审阅 R1") of
//! `docs/superpowers/specs/2026-09-18-v1-api-auth-plugin-chain-design.md`
//! requires that the `state` carried by an OAuth login URL binds to the
//! initiating browser via a CSPRNG nonce delivered through the
//! `__Host-bcs_oauth_login` cookie. The server-side pending-state record
//! stores `state → (nonce hash, provider, exact callback, flow namespace,
//! TTL)`. A whole batch of provider states issued by one `/auth/url`
//! request share ONE browser nonce; any single fully-matching `consume`
//! atomically invalidates EVERY provider state in that batch.
//!
//! # Why a separate contract from `PendingOAuthLoginPort`?
//!
//! This is the AUTH-PLUGIN layer's view of the pending-login surface.
//! It mirrors the shape of the service-api port
//! `bcs_service_api::port::oauth::PendingOAuthLoginPort`, but lives in
//! `bcs-auth-api` so BCS's auth plugins can depend on a pending-login
//! store without depending on `bcs-service-api` — contract crates on
//! both sides stay independent, and `bootstrap::identity_*` (Task 11+)
//! glues a concrete plugin implementation (e.g.
//! `MemoryPendingOAuthLoginStore` / future DB-backed store) into the
//! service-api port by translating every field. No `From`/`Into` impls
//! are exposed across the crate boundary; the two types intentionally do
//! NOT share an interface.
//!
//! # Storage-failure vs invalid-state
//!
//! [`LoginStateError`] carries no SQL, no endpoint, no secret. Two
//! variants:
//!
//! - [`LoginStateError::Invalid`] — `state` unknown, expired, or any of
//!   nonce hash / provider / exact_callback / flow namespace mismatched.
//!   The caller-equivalent HTTP 400/401; the consume is rejected. The
//!   batch is NOT consumed by a mismatch (see
//!   [`PendingOAuthLoginStore::consume`] for the atomicity rule).
//! - [`LoginStateError::Unavailable`] — store failure (DB down,
//!   timeout). The caller cannot conclude the pending-login row state.
//!   Equivalent HTTP 503.
//!
//! The memory implementation in this task never returns `Unavailable`
//! (no failure modes beyond panics); the variant is part of the
//! contract so non-memory (DB-backed) stores can surface storage faults
//! without changing the trait surface.
//!
//! # One browser, one batch (cookie-enforced, not store-enforced)
//!
//! The store does NOT track "which browser a batch belongs to".
//! One-browser-one-batch is enforced by the cookie carrying the nonce:
//! a different browser has no matching cookie and therefore cannot
//! present the correct nonce. Re-issue rotates the nonce; the OLD
//! batch's states remain validity-checkable until their TTL expires
//! (no server-side browser tracking). Production multi-instance
//! deployment requires a DB-backed shared pending-login store (Task 15
//! gate); a memory store implies single-instance / verified fixed
//! routing.

use async_trait::async_trait;

/// TTL (seconds) of a pending-login batch: 5 minutes per spec §8.4 step 2.
pub const PENDING_LOGIN_TTL_SECS: u64 = 300;

/// Outcome of [`PendingOAuthLoginStore::issue_batch`]: a CSPRNG browser
/// nonce (to be delivered to the browser via the
/// `__Host-bcs_oauth_login` cookie — NEVER placed in URLs, JSON, logs,
/// or query params), the batch's absolute expiry timestamp, and
/// per-provider `(provider, state)` pairs to be encoded into each
/// provider's authorize URL.
///
/// # Batch identity
///
/// All `provider_states` issued by the same `issue_batch` call share
/// ONE batch identity: the same browser nonce and the same `expires_at`.
/// Any single fully-matching `consume` atomically consumes the ENTIRE
/// batch — see [`PendingOAuthLoginStore::consume`] for the atomicity
/// rule. A second `consume` against any other state in the same batch
/// fails with [`LoginStateError::Invalid`] after the batch is consumed.
///
/// # Nonce and state independence
///
/// `browser_nonce` and each provider `state` MUST be independent
/// CSPRNG values — neither equal to nor derivable from each other.
/// The memory store uses independent `uuid::Uuid::new_v4()` calls for
/// each. The nonce is returned here exclusively so the caller can write
/// the `__Host-bcs_oauth_login` cookie; the store keeps a SHA-256 hash
/// of the nonce.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LoginBatch {
    /// The CSPRNG browser nonce (32 hex chars from uuid v4). Must not
    /// be equal to or derivable from any state value. Stored ONLY as
    /// a SHA-256 hash in the pending-state record; the raw nonce is
    /// returned here exclusively so the caller can write the
    /// `__Host-bcs_oauth_login` cookie.
    pub browser_nonce: String,
    /// Unix-seconds absolute expiry. Equal to `now + 300` at issue time
    /// (5-minute TTL per spec §8.4 step 2).
    pub expires_at: u64,
    /// Per-provider `(provider_name, state)` pairs. The state is a
    /// CSPRNG random value (uuid v4 hex), one per provider. Length
    /// matches the `providers` slice passed to `issue_batch`.
    pub provider_states: Vec<(String, String)>,
}

/// Pending-login state store error taxonomy. Two variants, both
/// payload-free to keep callers from leaking storage internals to
/// clients.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum LoginStateError {
    /// Invalid state: unknown, expired, or nonce hash / provider /
    /// exact_callback / flow namespace mismatch. The pending-login
    /// flow rejects with HTTP 400/401; the batch is NOT consumed by a
    /// mismatch (see [`PendingOAuthLoginStore::consume`] for the
    /// atomicity rule).
    #[error("invalid login state")]
    Invalid,
    /// Store unavailable: storage failure (DB down, timeout). The
    /// caller cannot conclude the pending-login row state. HTTP 503.
    /// The memory implementation does not return this variant.
    #[error("login state store unavailable")]
    Unavailable,
}

/// Browser-bound pending OAuth login state store.
///
/// Stores per-state pending-login records keyed by `state`, each
/// binding: nonce hash, provider, exact callback URL, flow namespace,
/// and absolute expiry. One browser session holds ONE batch at a time
/// (spec §8.4). [`Self::issue_batch`] allocates a fresh browser nonce
/// plus per-provider `state` values; [`Self::consume`] validates a
/// callback's `(state, browser_nonce, provider, exact_callback, flow)`
/// against the stored record and atomically consumes the WHOLE batch
/// on a full match.
///
/// # Atomicity rule (spec §8.4 "完成登录")
///
/// A `consume` call atomically (under a single lock / transaction that
/// covers both match-check AND batch removal) checks and, on a full
/// match, consumes the BATCH:
///
/// 1. **State lookup / expiry** — if `state` is unknown OR `now >
///    expires_at`, return `Err(Invalid)`. The unknown-state branch
///    does NOT consume any other batch (there is no batch to consume;
///    the state maps to no record). The expired-state branch likewise
///    does NOT consume other records of the same batch (the attacker
///    cannot burn a sibling state by waiting for TTL).
/// 2. **Nonce hash check (constant-time)** — if the SHA-256 of
///    `browser_nonce` does not match the stored hash, return
///    `Err(Invalid)` AND the batch is NOT consumed. This is the
///    cross-browser defense: browser B presenting browser A's legal
///    state with the wrong nonce must not burn A's pending state. After
///    this failure, browser A can still complete its own login with
///    the correct nonce.
/// 3. **Provider / exact_callback / flow namespace check** — if
///    `state` is known and the nonce hash matches but the provider,
///    exact_callback URL, or flow namespace does not match the stored
///    record, return `Err(Invalid)` AND the batch is NOT consumed.
///    Each `state` is bound to its provider within the batch: using
///    state-for-GITHUB against a callback for GOOGLE must fail,
///    regardless of nonce correctness.
/// 4. **Full match → consume** — only when state, nonce hash,
///    provider, exact_callback, flow all match, AND the record has
///    not expired, is the consume successful. The same lock that
///    performs the match-check MUST remove every provider state record
///    belonging to the same batch atomically. A subsequent consume
///    for any state in the same batch returns `Err(Invalid)` (state
///    unknown — they were removed together).
///
/// # Constant-time nonce hash comparison
///
/// Nonce hash comparison MUST be constant-time. The memory store uses
/// a manual constant-time compare over the fixed 32-byte SHA-256 digest
/// (no `subtle` crate dependency — workspace has none; the brief
/// disallows new deps without strong cause).
#[async_trait]
pub trait PendingOAuthLoginStore: Send + Sync {
    /// Allocate a fresh browser nonce + per-provider state values.
    /// The exact callback per provider is
    /// `format!("{callback_base}/{provider}")`; the STORE receives the
    /// per-provider exact callback on `consume`, while `issue_batch`
    /// receives the base. The TTL is fixed at 300 seconds
    /// (`now + PENDING_LOGIN_TTL_SECS`).
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<LoginBatch, LoginStateError>;

    /// Validate and atomically consume a pending-login record (and
    /// its whole batch on a full match). See trait docs for the
    /// atomicity rule.
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
