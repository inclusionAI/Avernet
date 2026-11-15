use super::common::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use std::{num::NonZeroU32, time::Duration};

#[derive(Debug, Clone, PartialEq)]
pub struct AdmitRequest {
    pub input: InputEnvelope,
    pub create_session: Option<SessionSpec>,
    pub create_task: Option<TaskSnapshot>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AdmitOutcome {
    Accepted(AdmissionReceipt),
    Duplicate(AdmissionReceipt),
    ContentConflict,
    Quarantined,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AppendAgentEvent {
    pub sid: Sid,
    pub tid: Tid,
    pub event: AgentEvent,
}

#[async_trait]
pub trait InboxProducerPort: Send + Sync {
    /// Success requires recoverable input, initial task/spec and durable activation.
    /// Retry completes partial activation before returning Duplicate. Conflicting
    /// content under the same scoped InputId never returns success.
    async fn admit(&self, request: AdmitRequest) -> Result<AdmitOutcome, StoreError>;
    /// Intermediate events only; retry keeps (did, holder epoch, seq) unchanged.
    /// Success also requires activation, even if no notification can be delivered.
    async fn append_stream(&self, request: AppendAgentEvent) -> Result<InboxPos, StoreError>;
    async fn get_spec(&self, sid: &Sid) -> Result<Option<SessionSpec>, StoreError>;
}

/// Ephemeral acknowledgement identity; not serializable or a recovery cursor.
/// ```compile_fail
/// use bcs_service_api::port::repo::distributed::inbox::DeliveryReceipt;
/// fn persist(receipt: DeliveryReceipt) { let _ = serde_json::to_string(&receipt); }
/// ```
#[derive(Debug, PartialEq, Eq)]
pub struct DeliveryReceipt {
    pub backend: BackendId,
    pub consumer_generation: String,
    pub opaque: String,
}

#[derive(Debug)]
pub struct SessionBatch {
    pub sid: Sid,
    pub entries: Vec<InputEnvelope>,
    /// May advance across duplicate physical records even when entries is empty.
    pub read_through: InboxPos,
    pub delivery: DeliveryReceipt,
}

#[derive(Debug)]
pub struct FeedBatch {
    pub sessions: Vec<SessionBatch>,
    pub hints: Vec<Sid>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadLimits {
    pub max_sessions: NonZeroU32,
    pub max_entries: NonZeroU32,
    pub max_bytes: NonZeroU32,
}

#[async_trait]
pub trait ShardFeedPort: Send {
    /// None means earliest retained required input, never "tail only".
    async fn watch(&mut self, sid: Sid, after: Option<InboxPos>) -> Result<(), StoreError>;
    async fn unwatch(&mut self, sid: &Sid) -> Result<(), StoreError>;
    /// Ordered within each session; physical duplicates are classified by provider.
    async fn next(
        &mut self,
        limits: ReadLimits,
        timeout: Duration,
    ) -> Result<FeedBatch, StoreError>;
}

#[async_trait]
pub trait InboxConsumerPort: Send + Sync {
    async fn open(&self, shard: ShardId) -> Result<Box<dyn ShardFeedPort>, StoreError>;
    async fn has_unconsumed(&self, sid: &Sid, after: Option<&InboxPos>)
        -> Result<bool, StoreError>;
    /// Validate committed coverage. Shared physical partitions can acknowledge
    /// only a contiguous committed prefix, never skip another session's work.
    async fn confirm_committed(
        &self,
        delivery: &DeliveryReceipt,
        proof: &CommitReceipt,
    ) -> Result<(), StoreError>;
    async fn trim_committed(&self, proof: &CommitReceipt) -> Result<(), StoreError>;
    /// Includes future deadlines. Takeover must activate these sessions, not just seal.
    async fn enumerate_pending(
        &self,
        shard: ShardId,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScheduleToken {
    pub sid: Sid,
    pub owner_epoch: WorkerEpoch,
    pub opaque: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScheduleObservation {
    Observed(ScheduleToken),
    Fenced,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SettleSession {
    pub owner: SessionOwnership,
    pub observed: ScheduleToken,
    pub next_check: Option<TimestampMillis>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SettleOutcome {
    Applied,
    NeedsRecheck,
    Fenced,
}

#[async_trait]
pub trait InboxSchedulingPort: Send + Sync {
    /// Install/confirm the monotonic scheduling owner fence BEFORE checking work.
    async fn observe(&self, owner: &SessionOwnership) -> Result<ScheduleObservation, StoreError>;
    /// Atomic token AND owner check. Every admitted batch invalidates prior tokens,
    /// including no-op due-time changes. NeedsRecheck requires a fresh decision.
    async fn settle(&self, request: SettleSession) -> Result<SettleOutcome, StoreError>;
    /// Bounded, fair paging; busy low-score members must not starve other sessions.
    async fn due(
        &self,
        shard: ShardId,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError>;
}
