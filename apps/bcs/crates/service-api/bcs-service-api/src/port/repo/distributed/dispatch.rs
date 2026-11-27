use super::common::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use std::{num::NonZeroU32, time::Duration};

#[derive(Debug, Clone, PartialEq)]
pub struct CreateCommittedDispatch {
    pub source: EffectSource,
    pub spec: DispatchSpec,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedCancelIntent {
    pub source: EffectSource,
    pub did: Did,
    pub intent: CancelIntent,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommittedDispatchInput {
    pub source: EffectSource,
    pub did: Did,
    pub input: PendingInput,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlOutcome {
    Applied,
    AlreadyApplied,
    ContentConflict,
    SupersededTerminal {
        outcome: FinalDispatchOutcome,
        evidence: String,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct DispatchRecord {
    pub view: DispatchView,
    pub revision: Revision,
    /// Retained evidence for the definitive terminal outcome. Unknown remote
    /// work has no terminal evidence; Supervisor must not fabricate an outcome.
    pub terminal_evidence: Option<String>,
    /// The verification handoff currently in force (plan T7): the Supervisor
    /// routes a record carrying one to the verification queue instead of a
    /// normal execution hint.
    pub verification: Option<VerificationHandoff>,
    /// The durable `Quarantined` record (plan T7): the stop-verification
    /// deadline passed with the outcome still unknown. Independent of the
    /// terminal threshold; readers expose the retained WORKING state with a
    /// `stop_unconfirmed`-class snapshot, never a fabricated outcome.
    pub quarantined: Option<QuarantineRecord>,
}

#[async_trait]
pub trait DispatchControlPort: Send + Sync {
    /// Unknown on legacy adapters; full phase-one assembly requires one shared identity.
    fn scan_budget_profile(&self) -> Option<super::budget::ScanBudgetProfile> {
        None
    }
    /// Success includes durable recheck registration, repaired on an existing row.
    /// Equal did with changed immutable content is a conflict, not successful NX.
    async fn create(&self, request: CreateCommittedDispatch)
    -> Result<IdempotentWrite, StoreError>;
    /// Monotonic intent: an authorized later request with a distinct ID shares
    /// an existing cancellation obligation without replacing its identity or
    /// resetting an observation. A retained successful request ID with different
    /// content conflicts. A new request against proven terminal state returns
    /// the terminal outcome and evidence; source/effect replay checks still apply.
    async fn request_cancel(
        &self,
        request: CommittedCancelIntent,
    ) -> Result<ControlOutcome, StoreError>;
    /// Deduplication survives dequeue, using retained identity/content evidence.
    async fn push_input(
        &self,
        request: CommittedDispatchInput,
    ) -> Result<ControlOutcome, StoreError>;
    async fn notify(&self, pool: &PoolId, did: &Did) -> Result<(), StoreError>;
    async fn get(&self, did: &Did) -> Result<Option<DispatchRecord>, StoreError>;
    /// Unresolved remote cancellation/unknown sends remain supervised after task end.
    async fn scan_due(
        &self,
        shard: ShardId,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Did>, StoreError>;
    /// Conditional recheck settlement; stale scans cannot erase newer obligations.
    async fn reschedule(
        &self,
        did: &Did,
        expected: Revision,
        at: TimestampMillis,
    ) -> Result<RecheckOutcome, StoreError>;
    /// Register this did in the verification queue index (plan T7). The
    /// queue is a repairable index over the authoritative dispatch state,
    /// never a second ownership source: it may list too much, never too
    /// little. The Supervisor calls this from its scan of the authoritative
    /// state when it finds a verification-mode dispatch whose index entry is
    /// missing (a crash between the handoff CAS and the index write).
    async fn index_verification(
        &self,
        _did: &Did,
        _due: TimestampMillis,
    ) -> Result<(), StoreError> {
        Err(StoreError::NotApplied {
            code: "verification_queue_unsupported".into(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecheckOutcome {
    Applied,
    NeedsRecheck,
    Ended,
}

pub enum ClaimOutcome {
    Acquired(Box<dyn HeldDispatchPort>),
    OwnedByMe(Holder),
    HeldByOther(Holder),
    Ended,
    NotFound,
    /// The dispatch is in verification mode (plan T7): normal execution
    /// claims, repeated hints and no-actor rebuild paths are all refused;
    /// the caller routes the did to the verification queue instead.
    Verification,
}

/// The draft of one verification handoff (plan T7): the current claim
/// fence protects the CAS that registers these facts all at once.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerificationHandoffDraft {
    /// The attempt of the send whose outcome stayed unknown.
    pub attempt: u32,
    /// The stable query key: the original request id, or the accepted
    /// remote task reference once one was recorded.
    pub key: VerificationKey,
    /// The approved verification method.
    pub method: VerificationMethod,
    /// When the next verification attempt is due — also the pending
    /// scheduling obligation, so scans of the authoritative state never
    /// miss a handed-off call.
    pub next_due: TimestampMillis,
    /// Backoff between verification attempts, in milliseconds.
    pub backoff_ms: u64,
}

/// One bounded verification claim (plan T7). Claims through this request
/// advance the independent verification epoch; the original Connector may
/// still be alive.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerificationClaimRequest {
    pub did: Did,
    pub me: Instance,
    pub request_id: RequestId,
}

/// The outcome of one verification claim. The acquired handle is a
/// restricted holder: approved query/cancel and persistent reports only;
/// start, resend and continuation are refused.
pub enum VerificationClaimOutcome {
    /// The claim advanced one epoch and is durable; the old epoch's writes
    /// are fenced from now on. A claim by the same instance also advances
    /// the epoch (the issue rate is one at a time per instance, and the
    /// taken-over handle is fenced by the new epoch).
    Acquired(Box<dyn HeldDispatchPort>),
    /// Another instance took the epoch first and holds it.
    ClaimedBy(VerificationClaim),
    Ended,
    NotFound,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DispatchClaimRequest {
    pub did: Did,
    pub me: Instance,
    pub request_id: RequestId,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResumeOwnedDispatch {
    pub did: Did,
    pub previous: Holder,
    pub request_id: RequestId,
}

#[async_trait]
pub trait DispatchQueuePort: Send + Sync {
    async fn claim(&self, request: DispatchClaimRequest) -> Result<ClaimOutcome, StoreError>;
    /// Only after the local actor is stopped/joined. Atomically advances generation.
    async fn resume_owned(&self, request: ResumeOwnedDispatch) -> Result<ClaimOutcome, StoreError>;
    /// Registers or re-registers a due verification entry in the queue
    /// index (plan T7): the index is a repairable over-approximation of the
    /// authoritative dispatch state, never an ownership source.
    async fn index_verification(
        &self,
        _did: &Did,
        _due: TimestampMillis,
    ) -> Result<(), StoreError> {
        Err(StoreError::NotApplied {
            code: "verification_queue_unsupported".into(),
        })
    }
    async fn notifications(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn CommandFeedPort>, StoreError>;
    /// The due page of the verification queue index (plan T7): entries whose
    /// due time has passed, oldest first. The index is repairable from the
    /// authoritative dispatch state alone; a claim must re-check the
    /// document before it takes anything.
    async fn due_verifications(
        &self,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Did>, StoreError> {
        let _ = now;
        let _ = page;
        Err(StoreError::NotApplied {
            code: "verification_queue_unsupported".into(),
        })
    }
    /// One bounded verifier claim (plan T7): advances the independent
    /// verification epoch of the dispatch's handoff and returns a restricted
    /// holder that fences every write by that epoch.
    async fn claim_verification(
        &self,
        request: VerificationClaimRequest,
    ) -> Result<VerificationClaimOutcome, StoreError> {
        let _ = request;
        Err(StoreError::NotApplied {
            code: "verification_queue_unsupported".into(),
        })
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct CommandDelivery {
    pub did: Did,
    pub backend: BackendId,
    pub consumer_generation: String,
    pub opaque: String,
}

#[async_trait]
pub trait CommandFeedPort: Send {
    /// Duplicate notifications and provider-specific redelivery are internal here.
    async fn receive(
        &mut self,
        max: NonZeroU32,
        timeout: Duration,
    ) -> Result<Vec<CommandDelivery>, StoreError>;
    /// Reliable takeover/no-action determination, NOT remote completion.
    async fn ack(&mut self, delivery: &CommandDelivery) -> Result<(), StoreError>;
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum HolderError {
    #[error("dispatch holder lost")]
    Lost,
    #[error("invalid dispatch transition: {code}")]
    InvalidTransition { code: String },
    #[error(transparent)]
    Store(#[from] StoreError),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SendOperation {
    Start {
        request_id: RequestId,
    },
    Input {
        seq: InputSeq,
        request_id: RequestId,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SendPermit {
    pub did: Did,
    pub holder: Holder,
    pub operation: SendOperation,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AcceptanceEvidence {
    pub operation: SendOperation,
    pub remote: RemoteTaskRef,
}

#[async_trait]
pub trait HeldDispatchPort: Send {
    fn view(&self) -> &DispatchView;
    fn holder(&self) -> &Holder;
    async fn refresh(&mut self) -> Result<DispatchView, HolderError>;
    /// Persist possible-send intent, with the durable proof of exactly
    /// what the attempt is, before any network call (plan T6). Every write
    /// fences holder.
    async fn begin_send(
        &mut self,
        operation: SendOperation,
        proof: SendProof,
    ) -> Result<SendPermit, HolderError>;
    /// Record a definitive start observation. A strictly proven `NotSent`
    /// can reopen the same immutable attempt; ambiguous outcomes never do.
    async fn record_node_start(
        &mut self,
        _delivery: DeliveryState,
        _retry_not_sent: bool,
    ) -> Result<(), HolderError> {
        Err(HolderError::InvalidTransition { code: "node_start_unsupported".into() })
    }
    /// Persist the first real response for a stable interaction before
    /// admitting its public/reducer-facing event. Synthetic errors do not use this.
    async fn record_node_response(
        &mut self,
        _response: NodeResponseEvidence,
    ) -> Result<(), HolderError> {
        Err(HolderError::InvalidTransition { code: "node_response_unsupported".into() })
    }
    async fn record_acceptance(
        &mut self,
        evidence: AcceptanceEvidence,
    ) -> Result<Option<CancelIntent>, HolderError>;
    /// Acknowledges a continuous prefix of definitively accepted inputs only.
    async fn confirm_input(
        &mut self,
        seq: InputSeq,
        evidence: AcceptanceEvidence,
    ) -> Result<IdempotentWrite, HolderError>;
    async fn record_cancel(
        &mut self,
        observation: CancelObservation,
    ) -> Result<IdempotentWrite, HolderError>;
    /// Unknown cannot be automatically promoted to an ended dispatch.
    async fn finish(
        &mut self,
        outcome: FinalDispatchOutcome,
        evidence: String,
    ) -> Result<IdempotentWrite, HolderError>;
    /// The claim-fenced verification handoff CAS (plan T7). Registers the
    /// verification mode, the attempt identity, the stable query key, the
    /// approved method, the next due time and backoff — atomically, under
    /// the holder fence, together with the pending scheduling obligation —
    /// and moves the holder line so the old holder's further writes are
    /// refused. The actor may exit only after the write is confirmed or
    /// read back (the transition machinery resolves an unknown answer).
    async fn hand_off_verification(
        &mut self,
        _draft: VerificationHandoffDraft,
    ) -> Result<IdempotentWrite, HolderError> {
        Err(HolderError::InvalidTransition {
            code: "verification_handoff_unsupported".into(),
        })
    }
    /// Records the durable `Quarantined` state (plan T7) through the
    /// verifier's own fence: the stop-verification deadline passed with the
    /// outcome still unknown. Idempotent; never moves the phase to Ended.
    async fn record_quarantine(
        &mut self,
        _at: TimestampMillis,
        _reason: &str,
    ) -> Result<IdempotentWrite, HolderError> {
        Err(HolderError::InvalidTransition {
            code: "quarantine_unsupported".into(),
        })
    }
}
