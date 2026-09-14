//! Transport-neutral data for managed message delivery. A lane uses the real
//! canonical Session, not an IM conversation or a protocol-v2 compatibility key.

use serde::{Deserialize, Serialize};

use crate::DeliveryType;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryFlowKind {
    Group,
    DirectA2a,
    Task,
    System,
    StateMachine,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct DeliveryLaneKey {
    pub env: String,
    pub target_bot_id: String,
    pub session_id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageDeliveryStatus {
    Queued,
    Dispatching,
    Running,
    Unknown,
    Cancelling,
    CancelUnknown,
    Completed,
    Failed,
    Cancelled,
    Expired,
    RejectedCapacity,
    PendingContext,
    Bound,
    Consumed,
    DiscardedContext,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryWaitReason {
    PriorMessageRunning,
    BotCapacity,
    RateLimited,
    BotOffline,
    RetryBackoff,
    Paused,
}

/// The lifecycle subset of a persisted delivery. Run/request identity and
/// scope checks belong to the caller before it supplies trusted evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct MessageDeliveryState {
    pub kind: DeliveryType,
    pub status: MessageDeliveryStatus,
    pub state_version: u64,
    /// Conservative send-start marker. Only proof of no downstream I/O may
    /// clear it; timeout, disconnect and restart must not clear it.
    pub may_have_been_sent: bool,
}

/// Durable delivery record. Canonical body and attachments live exclusively in
/// bcs_messages. Optional run fields are absent for context-only Inject rows.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersistedMessageDelivery {
    pub delivery_id: String,
    pub env: String,
    pub source_message_id: String,
    pub target_bot_id: String,
    pub session_id: String,
    pub group_id: String,
    pub source_session_seq: i64,
    pub flow_kind: DeliveryFlowKind,
    #[serde(flatten)]
    pub state: MessageDeliveryState,
    pub wait_reason: Option<DeliveryWaitReason>,
    pub available_at_ms: i64,
    pub expire_at_ms: Option<i64>,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
    pub run_id: Option<String>,
    pub idempotency_key: Option<String>,
    pub attempt_no: u32,
    pub request_id: Option<String>,
    pub send_started_at_ms: Option<i64>,
    pub submitted_at_ms: Option<i64>,
    pub accepted_at_ms: Option<i64>,
    pub run_deadline_at_ms: Option<i64>,
    pub terminal_at_ms: Option<i64>,
    pub bound_to_delivery_id: Option<String>,
    pub cancel_requested_at_ms: Option<i64>,
    pub cancel_requested_by: Option<String>,
    pub cancel_reason: Option<String>,
    pub abort_request_id: Option<String>,
    pub abort_started_at_ms: Option<i64>,
    pub cancel_deadline_at_ms: Option<i64>,
    pub last_error_code: Option<String>,
    /// Versioned, target-specific routing intent, never a persisted wire frame
    /// or a copy of canonical text/attachments. Admission validates its schema.
    pub semantic_projection_json: serde_json::Value,
    /// Original send-time downstream identity (no tokens or bypass headers).
    pub transport_context_json: Option<serde_json::Value>,
    /// Stable bounded-history selection; references/UTF-8 offsets only, no body.
    #[serde(default)]
    pub context_selection_json: Option<serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliveryContextSelection {
    pub version: u32,
    pub max_messages: u32,
    pub max_bytes: u64,
    pub bound_count: u64,
    pub history_bytes: u64,
    pub selected: Vec<SelectedDeliveryContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelectedDeliveryContext {
    pub delivery_id: String,
    pub state_version: u64,
    /// Byte offset in the projected canonical body, always a UTF-8 boundary.
    pub body_start: usize,
}
