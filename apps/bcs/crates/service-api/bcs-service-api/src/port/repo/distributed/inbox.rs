use super::common::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use serde::{Deserialize, Serialize};
use std::{num::NonZeroU32, time::Duration};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AdmitRequest {
    /// Trusted authenticated scope; checked against the immutable SessionSpec.
    pub scope: TaskScope,
    pub input: InputEnvelope,
    pub create_session: Option<SessionSpec>,
    pub create_task: Option<TaskSnapshot>,
}

/// Identifies one semantic report across Connector holders. Captured before payload offload.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConnectorEventIdentity {
    pub round: Option<u64>,
    pub state: String,
}
#[derive(Debug, Clone, PartialEq)]
pub struct ConnectorAdmission {
    pub input: InputEnvelope,
    pub identity: ConnectorEventIdentity,
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

/// The strict-round identity bound to one staged admission record: the
/// request id of the licensed round plus its checkpoint generation. The
/// record's Start feed entry is delivered only when the session's
/// checkpoint license names exactly these fields and the record's task.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StrictRoundIdentity {
    pub request_id: String,
    pub generation: u64,
}

/// Terminal state of one strict-round staging visit: a durable staged
/// record still waiting for its checkpoint license (pre-writes are durable
/// repairs and stay private), or the delivered admission outcome of a
/// record whose license already covers it.
#[derive(Debug, Clone, PartialEq)]
pub enum StrictRoundStage {
    Staged,
    Delivered(AdmitOutcome),
}

#[async_trait]
pub trait InboxProducerPort: Send + Sync {
    /// Read the single scoped admission document. A record is not a success
    /// receipt: callers must still confirm/repair via admit.
    /// Unsupported legacy providers fail explicitly rather than pretend absent.
    async fn lookup(&self, _scope: &TaskScope, _id: &InputId) -> Result<Option<AdmitRequest>, StoreError> {
        Err(StoreError::Invalid { code: "admission_lookup_unsupported".into() })
    }

    /// Report an existing dispatch into its frozen Session scope. A replay from another
    /// holder can carry different payload bytes; the first accepted report wins.
    async fn admit_connector(
        &self,
        _request: ConnectorAdmission,
    ) -> Result<AdmitOutcome, StoreError> {
        Err(StoreError::Invalid {
            code: "connector_admission_unsupported".into(),
        })
    }

    /// Optional shared scan-budget profile used by Worker startup validation.
    fn scan_budget_profile(&self) -> Option<super::budget::ScanBudgetProfile> {
        None
    }
    /// Success requires recoverable input, initial task/spec and durable activation.
    /// Retry completes partial activation before returning Duplicate. Conflicting
    /// content under the same scoped InputId never returns success. Critical
    /// Agent InputId uses the provider's documented stable did/remote-event ID
    /// algorithm and is verified before journaling; holder/sequence are excluded.
    async fn admit(&self, request: AdmitRequest) -> Result<AdmitOutcome, StoreError>;
    /// Intermediate events only; retry keeps (did, holder epoch, seq) unchanged.
    /// Success also requires activation, even if no notification can be delivered.
    async fn append_stream(&self, request: AppendAgentEvent) -> Result<InboxPos, StoreError>;

    /// Stage (or reconcile) the durable inbox record of one strict-group
    /// round: the record's pre-writes are durable repairs, and its Start
    /// feed entry is delivered only when the session's checkpoint license
    /// names exactly this request id, generation and the record's task. The
    /// request may carry the frozen `create_session` pre-write of a fresh
    /// session; a store must retain the first identity it ever staged — a
    /// reconciliation pass built without the pre-write adopts the record's
    /// staged one instead of answering a content conflict. The default
    /// refusal keeps unconfigured stores honest: a strict round never
    /// reaches a generic admission record through this path.
    async fn stage_strict_round(
        &self,
        _request: AdmitRequest,
        _round: StrictRoundIdentity,
    ) -> Result<StrictRoundStage, StoreError> {
        Err(StoreError::NotApplied {
            code: "strict_round_staging_unconfigured".into(),
        })
    }
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionFeedFailure {
    pub sid: Sid,
    pub error: StoreError,
}

#[derive(Debug)]
pub struct FeedBatch {
    pub sessions: Vec<SessionBatch>,
    pub hints: Vec<Sid>,
    /// Failed sessions are suspended until watch rebuilds from durable consumed.
    /// The caller must handle these failures; other sessions remain readable.
    pub failures: Vec<SessionFeedFailure>,
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

/// A pending page was not read; retry from the same cursor when resources free up.
pub const PENDING_SCAN_DEFERRED: &str = "pending_scan_deferred";

#[async_trait]
pub trait InboxConsumerPort: Send + Sync {
    fn reader_resources(&self) -> Option<InboxReaderResources> {
        None
    }
    /// Unknown on legacy adapters; full phase-one assembly requires one shared identity.
    fn scan_budget_profile(&self) -> Option<super::budget::ScanBudgetProfile> {
        None
    }
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
    /// `limit` is an upper bound: budgets may reduce the page size. Follow `next`
    /// even if fewer items were returned. `NotApplied` with `PENDING_SCAN_DEFERRED`
    /// means no page was read and `after` must be retried.
    async fn enumerate_pending(
        &self,
        shard: ShardId,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InboxReaderResources {
    pub max_document_bytes: usize,
    pub max_readers: usize,
    pub max_watches: usize,
    pub max_receipts: usize,
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
    /// Unknown on legacy adapters; full phase-one assembly requires one shared identity.
    fn scan_budget_profile(&self) -> Option<super::budget::ScanBudgetProfile> {
        None
    }
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
