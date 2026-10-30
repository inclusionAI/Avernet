//! History-backfill control wire (spec §6.8 / §10.10.1 / review-V P1-3):
//! the `chat.inject kind=history_complete` CONTROL variant. Complementary
//! to the missing-message injections (which ride the ordinary
//! message-id-deduplicated channels): this is the SNAPSHOT ATTESTATION the
//! Gateway must explicitly confirm (`history_ready`) before the protected
//! dispatch's original send may be re-driven. No business message, never
//! triggers the model, never carries credentials: the payload is the exact
//! non-credential manifest metadata the protected wire already carries —
//! snapshot id, cutoff, projection version, the FULL selected message id
//! list and the manifest digest covering every batch.

use async_trait::async_trait;

/// One history_complete snapshot attestation toward the dispatch's own
/// Caller target. Bot/session reuse the delivery envelope; subject routes inside
/// the Bot exactly as protected send does, without forwarding its credential.
#[derive(Debug, Clone)]
pub struct HistoryCompleteCommand {
    /// The snapshot the manifest names — the id list must cover EVERY batch
    /// of this snapshot; the digest covers the full list (spec §1924:
    /// the last batch can never stand in for the whole list).
    pub session_id: String,
    /// Destination employee subject, routing only; never an execution credential.
    pub caller_subject: super::CallerOpaqueRef,
    pub snapshot_id: String,
    pub history_cutoff_seq: u64,
    pub projection_version: u64,
    pub message_ids: Vec<String>,
    pub manifest_digest: String,
    pub now_ms: i64,
}

/// Closed error taxonomy: `Unavailable{retryable:true}` keeps the barrier
/// parked for another backoff round. NotSent and non-retryable refusals
/// converge the original delivery as TransportRejected, never by
/// re-admitting a new attempt. Deadline control remains the final bound
/// if the in-memory driver is lost or convergence cannot be committed.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum HistoryBackfillError {
    #[error("history backfill rejected before any network attempt")]
    NotSent,
    #[error("history backfill transport unavailable")]
    Unavailable { retryable: bool },
}

#[async_trait]
pub trait HistoryBackfillPort: Send + Sync {
    /// Replay one already-projected message using ordinary observe-only inject.
    /// The receiver deduplicates within Bot/session/subject/current environment.
    /// Legacy implementations refuse rather than attest bodies they never sent.
    async fn inject_history_message(
        &self,
        _target: &bcs_domain::BotDeliveryTarget,
        _service_credential: &bcs_domain::RedactedToken,
        _command: super::ObserveInjectCommand,
    ) -> Result<(), HistoryBackfillError> { Err(HistoryBackfillError::NotSent) }

    /// Inject one history_complete control attestation. The response's
    /// success must confirm application of the exact snapshot/digest in the addressed environment for this
    /// snapshot (spec §6.8: an empty snapshot must confirm too; gaps are
    /// legal and never substituted by a largest-received sequence).
    /// `history_ready` loss may idempotently re-send complete; repeated
    /// confirmation never starts the task.
    async fn inject_history_complete(
        &self,
        target: &bcs_domain::BotDeliveryTarget,
        service_credential: &bcs_domain::RedactedToken,
        command: HistoryCompleteCommand,
    ) -> Result<(), HistoryBackfillError>;
}
