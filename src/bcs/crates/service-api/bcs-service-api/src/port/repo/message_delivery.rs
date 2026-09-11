//! Atomic canonical-message and managed-delivery persistence. No network I/O.

use async_trait::async_trait;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{DeliveryType, NewMessage, PersistedMessage};

/// Bounded operational scans. Context expiry is independent of send dispatch.
#[derive(Debug, Clone, Copy)]
pub enum DeliveryWorkBatch { Expired, Control, Recovery }

/// Indexed identity/lane reads; never an environment-wide working set.
#[derive(Debug, Clone)]
pub enum DeliveryLookup {
    Id(String),
    Request(String),
    Run { bot: String, alias: String },
    Bound(String),
    Lane { bot: String, session: String },
    /// At most one unfinished Send; Inject must not block legacy drain.
    BotPending(String),
    /// At most 100 unbound contexts for drain cleanup after all Sends settle.
    BotPendingContexts(String),
    Message(String),
    Successor { bot: String, session: String, after_seq: i64, exclude: String, now_ms: i64 },
}

#[derive(Debug, Clone, Default)]
pub struct DeliveryQueueStatistic {
    pub flow_kind: String,
    pub kind: String,
    pub status: String,
    pub wait_reason: String,
    pub count: u64,
    pub oldest_created_at_ms: Option<i64>,
}

/// Newest bound metadata only. Count is complete; canonical bodies are not read.
pub struct BoundDeliveryContexts {
    pub rows: Vec<PersistedMessageDelivery>,
    pub total: u64,
}

#[derive(Debug, Clone)]
pub struct DeliveryAdmissionTarget {
    pub target_bot_id: String,
    pub kind: DeliveryType,
    pub max_queued: u32,
    pub semantic_projection_json: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct DeliveryDisplayMessage {
    pub message_id: String,
    pub message: NewMessage,
    pub event: Option<super::AppendEventRecord>,
}

#[derive(Debug, Clone)]
pub struct AdmitMessageDeliveries {
    /// Optional final visible chat segment, written immediately before an
    /// internal run_reply in the same transaction. Never admits targets.
    pub display_message: Option<DeliveryDisplayMessage>,
    pub message_id: String,
    pub message: NewMessage,
    pub flow_kind: DeliveryFlowKind,
    pub targets: Vec<DeliveryAdmissionTarget>,
    pub now_ms: i64,
    pub expire_at_ms: Option<i64>,
    /// Existing message.created event, when configured; committed atomically.
    /// Its public payload must use the safe history projection, not raw URLs.
    pub event: Option<super::AppendEventRecord>,
}

#[derive(Debug, Clone)]
pub struct DeliveryAdmissionResult {
    pub message: PersistedMessage,
    pub deliveries: Vec<PersistedMessageDelivery>,
    pub duplicate: bool,
}

#[derive(Debug, Clone)]
pub struct DeliveryCompareAndSet {
    pub expected_state_version: u64,
    pub delivery: PersistedMessageDelivery,
}

#[derive(Debug, thiserror::Error)]
pub enum MessageDeliveryRepoError {
    #[error("delivery state changed")]
    Conflict,
    #[error("invalid delivery operation: {0}")]
    Invalid(String),
    #[error("message delivery persistence failed: {0}")]
    Storage(String),
}

#[async_trait]
pub trait MessageDeliveryRepoPort: Send + Sync {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<BoundDeliveryContexts, MessageDeliveryRepoError>;
    /// Implementations must filter at storage; no fallback to full history.
    async fn lookup(&self, scope: DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError>;
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, MessageDeliveryRepoError>;
    /// Complete active count, never a count of a limited page.
    async fn active_count(&self, bot: &str) -> Result<u64, MessageDeliveryRepoError>;
    /// True when another active Send or an earlier unfinished Send occupies
    /// the lane. Scalar EXISTS; the candidate itself is excluded.
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, MessageDeliveryRepoError>;
    /// One oldest unfinished Send per lane, scalar projection only. Queued
    /// heads include backoff/expired entries so they cannot be overtaken.
    async fn queued_heads(&self, bot: &str, after_session: &str, limit: usize) -> Result<Vec<crate::core::message_delivery::DeliveryScheduleEntry>, MessageDeliveryRepoError>;
    /// Recovery uses an exclusive delivery-ID cursor. Expired and Control ignore
    /// `after`: successful actions leave the due set. Control reserves equal shares
    /// for dispatch timeout, run timeout, pending abort, and abort timeout, then
    /// lends unused capacity. Total <= min(limit, 200); deadlines sort before IDs
    /// within timeout classes. No global ordering across control classes.
    async fn work_batch(&self, kind: DeliveryWorkBatch, now_ms: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError>;
    /// Low-frequency SQL aggregation; scrape callers must cache the result.
    async fn queue_statistics(&self) -> Result<Vec<DeliveryQueueStatistic>, MessageDeliveryRepoError>;
    /// Environment-scoped business policy; absence means disabled version zero.
    async fn load_policy(&self) -> Result<bcs_config_api::message_delivery::DeliveryPolicyRecord, MessageDeliveryRepoError> {
        Err(MessageDeliveryRepoError::Storage("delivery policy repository unavailable".into()))
    }
    /// Atomically replace exactly expected_version, incrementing by one.
    async fn replace_policy(&self, expected_version: u64, record: bcs_config_api::message_delivery::DeliveryPolicyRecord) -> Result<(), MessageDeliveryRepoError> {
        let _ = (expected_version, record);
        Err(MessageDeliveryRepoError::Storage("delivery policy repository unavailable".into()))
    }
    fn is_durable(&self) -> bool {
        false
    }
    /// Scheduler working set; production implementations filter in SQL so
    /// completed history does not grow the cost of every scheduling tick.
    async fn list_unfinished_deliveries(
        &self,
    ) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
        Ok(self
            .list_deliveries(None)
            .await?
            .into_iter()
            .filter(|row| {
                !matches!(
                    row.state.status,
                    Status::Completed
                        | Status::Failed
                        | Status::Cancelled
                        | Status::Expired
                        | Status::RejectedCapacity
                        | Status::Consumed
                        | Status::DiscardedContext
                )
            })
            .collect())
    }
    async fn get_delivery(
        &self,
        id: &str,
    ) -> Result<Option<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        Ok(self
            .list_deliveries(None)
            .await?
            .into_iter()
            .find(|row| row.delivery_id == id))
    }
    /// Allocate the canonical sequence, append body/attachments, admit all
    /// targets and bind prior pending context in one transaction. Capacity
    /// rejection is a per-target durable outcome, not a transaction error.
    async fn admit(
        &self,
        command: AdmitMessageDeliveries,
    ) -> Result<DeliveryAdmissionResult, MessageDeliveryRepoError>;

    /// Scope is the store's configured env, never the current admission flags.
    async fn list_deliveries(
        &self,
        session_id: Option<&str>,
    ) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError>;

    /// Commit the primary transition and context changes together. If a Bot
    /// terminal has a canonical reply, its insertion/target admission belongs
    /// to this same transaction. Any version mismatch rolls everything back.
    async fn commit_transition(
        &self,
        changes: Vec<DeliveryCompareAndSet>,
        reply: Option<AdmitMessageDeliveries>,
    ) -> Result<Option<DeliveryAdmissionResult>, MessageDeliveryRepoError>;
}
