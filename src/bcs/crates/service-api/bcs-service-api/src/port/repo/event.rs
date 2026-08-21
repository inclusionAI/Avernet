//! Persistence contract for Event, Subscription, fanout, and Delivery state.
//!
//! Implementations must enforce environment isolation, scope-local epoch
//! serialization, immutable subscription revisions, stream sequence
//! allocation, claim leases, and strict-lane eligibility inside storage
//! transactions. This port exposes storage semantics, not SQL fragments.

use std::fmt;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::port::NewEvent;
use crate::types::{
    EventActor, EventDeliveryStatus, EventEnvelope, EventPayloadMode, EventSubscriptionScope,
    EventSubscriptionStatus,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventFanoutStatus {
    Pending,
    Completed,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventFanoutTargetPurpose {
    Normal,
    CausalPrerequisite,
    ManualReplay,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventFanoutTargetStatus {
    Pending,
    Materialized,
    Cancelled,
    Failed,
}

#[derive(Debug, Clone)]
pub struct EventSubscriptionRecord {
    pub subscription_id: String,
    pub name: String,
    pub scope: EventSubscriptionScope,
    pub status: EventSubscriptionStatus,
    pub current_revision: u64,
    pub created_by: EventActor,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    pub deleted_at_ms: Option<u64>,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct EventSubscriptionRevisionRecord {
    pub subscription_id: String,
    pub revision: u64,
    pub event_filters: Vec<String>,
    pub payload_mode: EventPayloadMode,
    pub endpoint_url: String,
    pub request_timeout_ms: u64,
    pub activated_at_ms: u64,
    pub retired_at_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct CreateEventSubscriptionRecord {
    pub subscription: EventSubscriptionRecord,
    pub revision: EventSubscriptionRevisionRecord,
    /// Maximum number of pending or active subscriptions reserved by this
    /// scope. Repositories enforce this while holding the scope write lock.
    pub scope_limit: u32,
}

#[derive(Debug, Clone)]
pub struct CancelPendingEventSubscriptions {
    pub subscription_ids: Vec<String>,
    pub actor: EventActor,
    pub reason: String,
    pub cancelled_at_ms: u64,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct ReplaceEventSubscriptionRevision {
    pub subscription_id: String,
    pub expected_revision: u64,
    pub name: String,
    pub status: EventSubscriptionStatus,
    pub revision: EventSubscriptionRevisionRecord,
    pub cancel_retired_pending_deliveries: bool,
    pub actor: EventActor,
    pub reason: Option<String>,
    pub updated_at_ms: u64,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct ListEventSubscriptionRecords {
    pub scope: Option<EventSubscriptionScope>,
    pub status: Option<EventSubscriptionStatus>,
    pub after_subscription_id: Option<String>,
    pub limit: u32,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct AppendEventRecord {
    pub event: NewEvent,
    pub recorded_at: String,
    pub retention_until_ms: u64,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct EventRecord {
    pub envelope: EventEnvelope,
    pub producer: String,
    pub producer_key: String,
    pub fanout_status: EventFanoutStatus,
    pub retention_until_ms: u64,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct AppendEventRecordResult {
    pub event: EventRecord,
    pub fanout_target_ids: Vec<String>,
    pub deduplicated: bool,
}

#[derive(Debug, Clone)]
pub struct EventFanoutTargetRecord {
    pub target_id: String,
    pub event_id: String,
    pub subscription_id: String,
    pub subscription_revision: u64,
    pub purpose: EventFanoutTargetPurpose,
    pub replay_request_id: Option<String>,
    pub replay_of_delivery_id: Option<String>,
    pub depends_on_target_id: Option<String>,
    pub status: EventFanoutTargetStatus,
    pub created_at_ms: u64,
    pub materialized_at_ms: Option<u64>,
    pub cancelled_at_ms: Option<u64>,
    pub lease_owner: Option<String>,
    pub lease_until_ms: Option<u64>,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct ClaimFanoutTargets {
    /// Stable worker identity. The repository may mint a per-claim fencing
    /// token and returns it in each target's `lease_owner`.
    pub worker_id: String,
    pub now_ms: u64,
    pub lease_until_ms: u64,
    pub limit: u32,
    pub env: String,
}

#[derive(Clone)]
pub struct EventDeliveryRecord {
    pub delivery_id: String,
    pub fanout_target_id: String,
    pub event_id: String,
    pub event_type: String,
    pub subscription_id: String,
    pub subscription_revision: u64,
    pub stream_key: String,
    pub sequence: u64,
    pub payload_bytes: Vec<u8>,
    pub payload_sha256: String,
    pub status: EventDeliveryStatus,
    pub attempt_count: u32,
    pub first_attempt_at_ms: Option<u64>,
    pub last_attempt_at_ms: Option<u64>,
    pub next_attempt_at_ms: Option<u64>,
    pub lease_owner: Option<String>,
    pub lease_until_ms: Option<u64>,
    pub last_http_status: Option<u16>,
    pub last_error_category: Option<String>,
    pub last_error_summary: Option<String>,
    pub dead_lettered_at_ms: Option<u64>,
    pub cancelled_at_ms: Option<u64>,
    pub skipped_at_ms: Option<u64>,
    pub skip_actor: Option<EventActor>,
    pub skip_reason: Option<String>,
    pub replay_of_delivery_id: Option<String>,
    pub resolved_by_delivery_id: Option<String>,
    pub resolved_at_ms: Option<u64>,
    pub created_at_ms: u64,
    pub succeeded_at_ms: Option<u64>,
    pub env: String,
}

impl fmt::Debug for EventDeliveryRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EventDeliveryRecord")
            .field("delivery_id", &self.delivery_id)
            .field("fanout_target_id", &self.fanout_target_id)
            .field("event_id", &self.event_id)
            .field("event_type", &self.event_type)
            .field("subscription_id", &self.subscription_id)
            .field("subscription_revision", &self.subscription_revision)
            .field("stream_key", &self.stream_key)
            .field("sequence", &self.sequence)
            .field("payload_bytes", &"[REDACTED]")
            .field("payload_sha256", &"[REDACTED]")
            .field("status", &self.status)
            .field("attempt_count", &self.attempt_count)
            .field("first_attempt_at_ms", &self.first_attempt_at_ms)
            .field("last_attempt_at_ms", &self.last_attempt_at_ms)
            .field("next_attempt_at_ms", &self.next_attempt_at_ms)
            .field("lease_owner", &self.lease_owner)
            .field("lease_until_ms", &self.lease_until_ms)
            .field("last_http_status", &self.last_http_status)
            .field("last_error_category", &self.last_error_category)
            .field("last_error_summary", &self.last_error_summary)
            .field("dead_lettered_at_ms", &self.dead_lettered_at_ms)
            .field("cancelled_at_ms", &self.cancelled_at_ms)
            .field("skipped_at_ms", &self.skipped_at_ms)
            .field("skip_actor", &self.skip_actor)
            .field(
                "skip_reason",
                &self.skip_reason.as_ref().map(|_| "[REDACTED]"),
            )
            .field("replay_of_delivery_id", &self.replay_of_delivery_id)
            .field("resolved_by_delivery_id", &self.resolved_by_delivery_id)
            .field("resolved_at_ms", &self.resolved_at_ms)
            .field("created_at_ms", &self.created_at_ms)
            .field("succeeded_at_ms", &self.succeeded_at_ms)
            .field("env", &self.env)
            .finish()
    }
}

#[derive(Debug, Clone)]
pub struct MaterializeFanoutTarget {
    pub target_id: String,
    /// Exact fencing token returned in `EventFanoutTargetRecord::lease_owner`.
    pub expected_lease_owner: String,
    pub delivery: EventDeliveryRecord,
    pub materialized_at_ms: u64,
}

#[derive(Debug, Clone)]
pub struct ClaimEventDeliveries {
    /// Stable worker identity. The repository may mint a per-claim fencing
    /// token and returns it in each Delivery's `lease_owner`.
    pub worker_id: String,
    pub now_ms: u64,
    pub lease_until_ms: u64,
    pub limit: u32,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct RenewEventDeliveryLease {
    pub delivery_id: String,
    /// Exact fencing token returned in `EventDeliveryRecord::lease_owner`.
    pub expected_lease_owner: String,
    pub attempt_no: u32,
    pub now_ms: u64,
    pub lease_until_ms: u64,
    pub env: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventDeliveryAttemptRecordResult {
    Success,
    Retryable,
    Terminal,
}

#[derive(Debug, Clone)]
pub struct CompleteEventDeliveryAttempt {
    pub delivery_id: String,
    /// Exact fencing token returned in `EventDeliveryRecord::lease_owner`.
    pub expected_lease_owner: String,
    pub attempt_no: u32,
    pub started_at_ms: u64,
    pub completed_at_ms: u64,
    pub result: EventDeliveryAttemptRecordResult,
    pub next_status: EventDeliveryStatus,
    pub next_attempt_at_ms: Option<u64>,
    pub http_status: Option<u16>,
    pub error_category: Option<String>,
    pub error_summary: Option<String>,
    pub response_bytes_observed: u64,
}

#[derive(Debug, Clone)]
pub struct EventDeliveryAttemptRecord {
    pub delivery_id: String,
    pub attempt_no: u32,
    pub started_at_ms: u64,
    pub completed_at_ms: u64,
    pub latency_ms: u64,
    pub result: EventDeliveryAttemptRecordResult,
    pub http_status: Option<u16>,
    pub error_category: Option<String>,
    pub error_summary: Option<String>,
    pub response_bytes_observed: u64,
    pub worker_id: String,
}

#[derive(Debug, Clone)]
pub struct ListEventDeliveryRecords {
    pub subscription_id: Option<String>,
    pub event_id: Option<String>,
    pub status: Option<EventDeliveryStatus>,
    pub after_delivery_id: Option<String>,
    pub limit: u32,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct CreateEventReplayTarget {
    pub original_delivery_id: String,
    pub subscription_id: String,
    pub subscription_revision: u64,
    pub replay_request_id: String,
    pub target_id: String,
    pub actor: EventActor,
    pub reason: Option<String>,
    pub created_at_ms: u64,
    pub env: String,
}

#[derive(Clone)]
pub struct SkipDeadLetteredEventDelivery {
    pub delivery_id: String,
    pub actor: EventActor,
    pub reason: String,
    pub skipped_at_ms: u64,
    pub env: String,
}

impl fmt::Debug for SkipDeadLetteredEventDelivery {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SkipDeadLetteredEventDelivery")
            .field("delivery_id", &self.delivery_id)
            .field("actor", &self.actor)
            .field("reason", &"[REDACTED]")
            .field("skipped_at_ms", &self.skipped_at_ms)
            .field("env", &self.env)
            .finish()
    }
}

#[derive(Debug, Clone)]
pub struct EventRetentionRequest {
    pub now_ms: u64,
    pub event_limit: u32,
    pub audit_limit: u32,
    pub env: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EventRetentionResult {
    pub events_deleted: u64,
    pub deliveries_deleted: u64,
    pub attempts_deleted: u64,
    pub subscriptions_deleted: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum EventRepoError {
    #[error("invalid event repository input: {0}")]
    InvalidInput(String),
    #[error("event repository conflict: {0}")]
    Conflict(String),
    #[error("event repository resource limit reached: {0}")]
    LimitReached(String),
    #[error("event causation violation: {0}")]
    CausationViolation(String),
    #[error("event repository record not found: {0}")]
    NotFound(String),
    #[error("event repository lease lost: {0}")]
    LeaseLost(String),
    #[error("event repository operation is not supported: {0}")]
    Unsupported(String),
    #[error("event repository storage failure: {0}")]
    Storage(String),
}

#[async_trait]
pub trait EventRepoPort: Send + Sync {
    /// Create the subscription row, immutable revision 1, and increment the
    /// target scope's epoch in one transaction.
    async fn create_subscription(
        &self,
        record: CreateEventSubscriptionRecord,
    ) -> Result<EventSubscriptionRecord, EventRepoError>;
    /// Cancel only still-pending inline subscriptions. Implementations must
    /// never disable or delete a subscription that has already been activated.
    async fn cancel_pending_subscriptions(
        &self,
        command: CancelPendingEventSubscriptions,
    ) -> Result<u64, EventRepoError> {
        let _ = command;
        Err(EventRepoError::Unsupported(
            "pending Event Subscription cancellation is not configured".to_string(),
        ))
    }
    async fn get_subscription(
        &self,
        subscription_id: &str,
        env: &str,
    ) -> Result<Option<(EventSubscriptionRecord, EventSubscriptionRevisionRecord)>, EventRepoError>;
    /// Read the immutable revision fixed on a fanout target or Delivery. A
    /// worker must never substitute the Subscription's current revision.
    async fn get_subscription_revision(
        &self,
        subscription_id: &str,
        revision: u64,
        env: &str,
    ) -> Result<Option<EventSubscriptionRevisionRecord>, EventRepoError>;
    async fn list_subscriptions(
        &self,
        query: ListEventSubscriptionRecords,
    ) -> Result<Vec<EventSubscriptionRecord>, EventRepoError>;
    async fn replace_subscription_revision(
        &self,
        command: ReplaceEventSubscriptionRevision,
    ) -> Result<EventSubscriptionRecord, EventRepoError>;
    /// Allocate the stream sequence, persist the canonical Event, validate its
    /// causation edge, and snapshot every matching fanout target atomically.
    /// Implementations also guarantee producer-key idempotency.
    async fn append_event(
        &self,
        command: AppendEventRecord,
    ) -> Result<AppendEventRecordResult, EventRepoError>;
    async fn get_event(
        &self,
        event_id: &str,
        env: &str,
    ) -> Result<Option<EventRecord>, EventRepoError>;
    async fn claim_fanout_targets(
        &self,
        command: ClaimFanoutTargets,
    ) -> Result<Vec<EventFanoutTargetRecord>, EventRepoError>;
    async fn materialize_fanout_target(
        &self,
        command: MaterializeFanoutTarget,
    ) -> Result<EventDeliveryRecord, EventRepoError>;
    /// Claim only eligible Deliveries. Strict lanes expose only their head;
    /// unresolved dead letters and causal prerequisites remain blockers.
    async fn claim_deliveries(
        &self,
        command: ClaimEventDeliveries,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError>;
    /// Extend an in-flight Delivery lease while preserving the claim fencing
    /// token and attempt number. Expired or replaced claims must fail closed.
    async fn renew_delivery_lease(
        &self,
        command: RenewEventDeliveryLease,
    ) -> Result<EventDeliveryRecord, EventRepoError>;
    /// Persist the Attempt audit row, transition Delivery state, and release
    /// its lease atomically. The expected lease owner fences stale workers.
    async fn complete_delivery_attempt(
        &self,
        command: CompleteEventDeliveryAttempt,
    ) -> Result<EventDeliveryRecord, EventRepoError>;
    async fn get_delivery(
        &self,
        delivery_id: &str,
        env: &str,
    ) -> Result<Option<(EventDeliveryRecord, Vec<EventDeliveryAttemptRecord>)>, EventRepoError>;
    async fn list_deliveries(
        &self,
        query: ListEventDeliveryRecords,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError>;
    /// Idempotently create a manual-replay target after locking and validating
    /// the unresolved dead-lettered original Delivery.
    async fn create_replay_target(
        &self,
        command: CreateEventReplayTarget,
    ) -> Result<EventFanoutTargetRecord, EventRepoError>;
    /// Record an explicit data-loss acknowledgement and unblock the strict
    /// lane; only unresolved dead-lettered Delivery may be skipped.
    async fn skip_dead_lettered_delivery(
        &self,
        command: SkipDeadLetteredEventDelivery,
    ) -> Result<EventDeliveryRecord, EventRepoError>;
    /// Delete only records no longer referenced by fanout, retry, causation,
    /// blocked lanes, or compliance holds.
    async fn purge_expired(
        &self,
        command: EventRetentionRequest,
    ) -> Result<EventRetentionResult, EventRepoError>;
}
