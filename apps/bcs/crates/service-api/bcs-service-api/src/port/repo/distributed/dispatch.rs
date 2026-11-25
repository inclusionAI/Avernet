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
    async fn notifications(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn CommandFeedPort>, StoreError>;
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
    /// Persist possible-send intent before any network call. Every write fences holder.
    async fn begin_send(&mut self, operation: SendOperation) -> Result<SendPermit, HolderError>;
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
}
