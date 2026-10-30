//! Session speaking-lease repository port (spec §6.3 / §6.3.1 B7).
//!
//! Persistence contract for Human speaking rights over one session. Every
//! mutator is one atomic transaction/CAS: acquire, preempt, release and
//! message admission decide under the same serialization boundary, so a
//! submission can never slip past a preemption race. The persisted state
//! is the five-field business whitelist only — never credentials, proofs
//! or redeemable identity material.

use async_trait::async_trait;
use bcs_domain::speaking_lease::SessionSpeakingLease;

use crate::core::ServiceError;
use crate::types::ServiceResult;

/// Acquire (non-preemptive): an ordinary acquisition that never steals.
#[derive(Debug, Clone)]
pub struct AcquireLease<'a> {
    pub session_id: &'a str,
    /// Requesting business actor; per §6.3.1 every submission re-
    /// authenticates — the repository compares business actor ids only.
    pub requester_actor_id: &'a str,
    pub now_ms: i64,
    pub idle_timeout_ms: i64,
}

/// Preempt: transfer speaking rights to the requester regardless of the
/// current holder. Preemption only moves submission eligibility; by
/// contract it never cancels turns/runs/children of the previous holder.
#[derive(Debug, Clone)]
pub struct PreemptLease<'a> {
    pub session_id: &'a str,
    pub requester_actor_id: &'a str,
    pub now_ms: i64,
    pub idle_timeout_ms: i64,
}

/// Release: holder-initiated give-back, guarded by lease id and epoch —
/// a stale release instruction never touches a transferred lease.
#[derive(Debug, Clone)]
pub struct ReleaseLease<'a> {
    pub session_id: &'a str,
    pub holder_actor_id: &'a str,
    pub lease_id: &'a str,
    pub epoch: u64,
    pub now_ms: i64,
}

/// Message admission: the atomic boundary deciding whether a Human
/// message from `expected_holder` under `expected_lease_id`/`epoch` is
/// accepted and, when accepted, renewing the idle expiry (a successful
/// new-message acceptance is the ONLY renewal event).
#[derive(Debug, Clone)]
pub struct AdmitMessage<'a> {
    pub session_id: &'a str,
    pub expected_holder: &'a str,
    pub expected_lease_id: &'a str,
    pub expected_epoch: u64,
    pub now_ms: i64,
    pub idle_timeout_ms: i64,
}

/// Outcome of a non-preemptive acquire.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseAcquire {
    /// Granted a fresh lease (new lease id, next epoch).
    Granted(SessionSpeakingLease),
    /// The requester already holds a valid lease: idempotent re-entry,
    /// expiry NOT renewed and lease id/epoch NOT rotated.
    SelfHeld(SessionSpeakingLease),
    /// Another valid holder exists: an ordinary acquire never steals.
    Occupied {
        holder_actor_id: String,
        epoch: u64,
        expires_at_ms: i64,
    },
}

/// Outcome of a release attempt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LeaseRelease {
    /// Released a matching lease; later submissions need reacquisition.
    Released,
    /// No effect: holder, lease id or epoch did not match the current
    /// state (already released, superseded or never held).
    NotHeld,
}

/// Outcome of message admission.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseDecision {
    /// Accepted and the idle expiry renewed under the same lease id and
    /// epoch (a duplicate message replays its original acceptance and
    /// never renews — duplicate detection happens above this port).
    Admitted(SessionSpeakingLease),
    /// The lease exists with the same holder but a later epoch: the
    /// submission arrived after a preemption boundary.
    Superseded { current_epoch: u64 },
    /// The lease is known but held by another business actor.
    WrongHolder { holder_actor_id: String },
    /// No matching live lease under the expected id/epoch/holder: expired,
    /// released, or already rotated away.
    Lost,
}

#[async_trait]
pub trait SpeakingLeaseRepoPort: Send + Sync {
    /// Ordinary acquire. A valid foreign holder yields [`LeaseAcquire::
    /// Occupied`]; the requester's own valid lease yields idempotent
    /// [`LeaseAcquire::SelfHeld`] without renewal; anything else (absent
    /// or expired) grants a fresh lease with the next epoch.
    async fn acquire(&self, command: AcquireLease<'_>) -> ServiceResult<LeaseAcquire>;

    /// Preemptive acquisition: always transfers to the requester with a
    /// new lease id and the next epoch, regardless of the previous
    /// holder's validity. The first acquisition of a session is also an
    /// epoch-1 grant.
    async fn preempt(&self, command: PreemptLease<'_>) -> ServiceResult<SessionSpeakingLease>;

    /// Holder-initiated release guarded by lease id and epoch.
    async fn release(&self, command: ReleaseLease<'_>) -> ServiceResult<LeaseRelease>;

    /// Atomic admission + renewal decision for one Human message. `Lost`
    /// for absent/expired/superseded leases; accepted decisions renew the
    /// idle window under the same lease id/epoch.
    async fn admit_message(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision>;

    /// Pure validation WITHOUT renewal: the submission-eligibility fence
    /// applied before the message is durably accepted (spec §6.3 rule 2:
    /// a message that passed the fence before a preemption boundary is
    /// legal). The follow-up `admit_message` after acceptance performs
    /// the renewal; its late loss never un-accepts a legal message.
    /// Default refuses: repositories must implement the same decision
    /// ordering as admission minus the write.
    async fn check_submission(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision> {
        let _ = command;
        Err(ServiceError::InvalidOperation {
            message: "speaking-lease validation is not supported by this repository".to_string(),
            request_id: None,
        })
    }

    /// Current persisted lease of the session (raw, including an expired
    /// or superseded one) — a read-only projection for authenticated
    /// management queries; never an admission decision by itself.
    async fn current(&self, session_id: &str) -> ServiceResult<Option<SessionSpeakingLease>>;

    /// Storage availability probe: the caller-group admission path must
    /// fail closed when the lease store cannot decide. Plain groups never
    /// consult this port at all.
    async fn healthy(&self) -> ServiceResult<()> {
        Ok(())
    }
}

impl From<LeaseDecision> for Option<SessionSpeakingLease> {
    fn from(decision: LeaseDecision) -> Self {
        match decision {
            LeaseDecision::Admitted(lease) => Some(lease),
            _ => None,
        }
    }
}
