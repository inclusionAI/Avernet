//! One-shot trusted-identity handoff contracts (spec §7.5.2, P2-A).
//!
//! Between the HTTP worker that authenticated the Human and the (elected,
//! scheduler-owned) worker that actually dispatches, the runtime-only
//! identity may only travel through this bounded, in-memory, same-epoch
//! handoff. It is NEVER a durable queue: identity cannot be recovered from
//! the DB, a lease holder, or the message sender. All keys below are pure
//! coordination facts and structurally exclude principal/subject/token
//! material.
//!
//! Lifecycle (spec table):
//! 1. `reserve` — before the ordinary Managed admission, within a bounded
//!    capacity (reserved + published + claimed slots all count).
//! 2. `publish` — only a fresh Queued receipt may publish once. Duplicates,
//!    failed or uncertain admissions never back-fill or overwrite identity
//!    or extend validity.
//! 3. `claim` — exclusively by the first committed dispatch attempt; claiming
//!    consumes the slot. Failed, timed-out or later-cancelled claims never
//!    return identity; expiry while parked is a hard loss.
//! 4. `close` — irreversible (owner loss/degradation/shutdown). Post-close no
//!    results are handed out; a new epoch builds a fresh instance.

use async_trait::async_trait;

use crate::application::caller_identity::AuthenticatedHumanCallerIngress;

/// Directory key of one handoff slot: pure coordination facts, never
/// principal/subject/token material. `source_message_id` is the canonical
/// message id (turn), not any client-supplied id.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct HandoffScope {
    pub env: String,
    pub session_id: String,
    pub group_id: Option<String>,
    pub source_message_id: String,
    pub target_bot_id: String,
}

/// Facts of a fresh Queued admission receipt. Constructed by the trusted
/// admission path only; the handoff stores it for claim-side matching.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueuedAdmissionReceipt {
    pub delivery_id: String,
    /// Row version the admission committed; claims must match the attempt it
    /// transitions from for the first StartSend, not whatever is current.
    pub state_version: u64,
    /// Server admission time; not a validity source for the identity.
    pub admitted_at_ms: i64,
}

/// Facts of the dispatch attempt claiming a slot (Managed StartSend output).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HandoffClaim {
    pub delivery_id: String,
    /// Version read when the dispatcher selected the row; CAS pairing.
    pub state_version: u64,
    pub run_id: Option<String>,
    pub attempt: u32,
    /// Explicit caller clock: all parking/consumption is deterministic.
    pub now_ms: i64,
}

/// Closed, non-leaking failure set. No variant admits a callback into
/// identity-bearing text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, thiserror::Error)]
pub enum HandoffError {
    #[error("caller identity handoff capacity exhausted")]
    CapacityExhausted,
    #[error("caller identity handoff slot was never reserved")]
    NotReserved,
    #[error("caller identity handoff slot already published")]
    AlreadyPublished,
    #[error("caller identity handoff slot already claimed")]
    AlreadyClaimed,
    #[error("caller identity handoff is closed for this epoch")]
    Closed,
    #[error("caller identity handoff input invalid")]
    InvalidInput,
}

/// Read-only slot-state answer for the dispatch scheduler's pre-StartSend
/// probe (the plan-A readiness gate). The probe never mutates the slot and
/// never exposes identity material; it only tells which side of the
/// admission race a committed caller row is on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandoffIdentityReadiness {
    /// The identity is parked; a committed dispatch attempt may claim it.
    Published,
    /// The slot is reserved but the identity is not published yet: a
    /// same-process admission sits between its queue commit and its
    /// publish. The dispatcher must PARK the row and pick it up again
    /// later — converging now would permanently fail an accepted task
    /// whose identity arrives microseconds after the verdict.
    Reserved,
    /// No identity can ever arrive for this scope again (never published
    /// with the reservation lost/swept/sealed/closed, or an earlier claim
    /// consumed it). The existing fail-closed convergence owns this case.
    Gone,
}

impl QueuedAdmissionReceipt {
    pub fn new(delivery_id: impl Into<String>, state_version: u64, admitted_at_ms: i64) -> Self {
        Self {
            delivery_id: delivery_id.into(),
            state_version,
            admitted_at_ms,
        }
    }
}

impl HandoffClaim {
    pub fn new(
        delivery_id: impl Into<String>,
        state_version: u64,
        run_id: Option<String>,
        attempt: u32,
        now_ms: i64,
    ) -> Self {
        Self {
            delivery_id: delivery_id.into(),
            state_version,
            run_id,
            attempt,
            now_ms,
        }
    }
}

/// The one-shot identity handoff seam. Implementations must never persist
/// any slot content; Debug output of parameters and of the implementation
/// must never reveal principal material.
#[async_trait]
pub trait HumanCallerDeliveryHandoffService: Send + Sync {
    /// Reserve a bounded slot before admission. The reserve window is
    /// explicit and advisory; it bounds the pending slot and does not extend
    /// any identity validity.
    async fn reserve(&self, scope: &HandoffScope, reserve_ttl_ms: i64, now_ms: i64)
        -> Result<(), HandoffError>;

    /// Publish once against a fresh Queued receipt. Fails on repeated,
    /// overlapping or mismatched publication; never overwrites an already
    /// published identity.
    async fn publish(
        &self,
        scope: &HandoffScope,
        ingress: AuthenticatedHumanCallerIngress,
        receipt: QueuedAdmissionReceipt,
    ) -> Result<(), HandoffError>;

    /// Release an un-published reservation (admission failed or the caller
    /// gave up before committing). Only `Reserved` slots release; published
    /// or claimed identity is never un-parked.
    async fn release(&self, scope: &HandoffScope) -> Result<(), HandoffError>;

    /// Exclusively claim (and consume) the identity for the first committed
    /// dispatch attempt. Second claims, mismatched deliveries, post-close or
    /// past-expiry claims all fail; the identity is never returned to the
    /// slot. Consumers share an immutable identity handle; claim-once is
    /// enforced by each target slot, independently of ingress Clone.
    async fn claim(
        &self,
        scope: &HandoffScope,
        claim: HandoffClaim,
    ) -> Result<std::sync::Arc<AuthenticatedHumanCallerIngress>, HandoffError>;

    /// Read-only probe of one scope's slot for the dispatch scheduler's
    /// pre-StartSend gate: can identity still arrive for this scope?
    /// Unlike `claim` this never consumes, never sweeps and stays cheap
    /// (schedulers call it per caller row per tick). A `Reserved` answer
    /// means an admission is still in flight — the caller parks the row,
    /// never converges it.
    async fn identity_readiness(&self, scope: &HandoffScope) -> HandoffIdentityReadiness;

    /// Publish one identity across MULTIPLE target scopes (spec §6.2: one
    /// canonical Human message, one identity, per-target dispatch).
    /// Every (scope, receipt) pair must name a Reserved slot; the whole
    /// batch lands atomically under one lock or not at all. The one-shot
    /// semantics are per-scope: each target's slot seals on its own claim.
    async fn publish_for_targets(
        &self,
        ingress: AuthenticatedHumanCallerIngress,
        targets: &[(HandoffScope, QueuedAdmissionReceipt)],
    ) -> Result<(), HandoffError> {
        let _ = (ingress, targets);
        Err(HandoffError::InvalidInput)
    }

    /// Irreversibly close this epoch's handoff. In-flight results are
    /// dropped; no reopen exists.
    async fn close(&self);
}