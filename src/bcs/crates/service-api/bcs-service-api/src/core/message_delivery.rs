//! Pure lifecycle decisions. The application must persist the returned state
//! and context changes atomically before applying any I/O or releasing slots.

use bcs_domain::message_delivery::{MessageDeliveryState, MessageDeliveryStatus};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryLifecycleEvent {
    /// Authorization to start one attempt; not evidence of downstream receipt.
    StartSend,
    /// Transport submission is not proof of Bot receipt.
    Submitted,
    Accepted,
    /// Caller has matched the current request and proved no I/O or residual
    /// sender task exists. `retry` includes the application's retry-budget check.
    DefinitelyNotSent {
        retry: bool,
    },
    TransportUnknown,
    CancelRequested,
    /// Explicit operator/user scope abort, never a scheduler retry.
    ScopeAbortRequested,
    /// Authorize the first abort attempt after cancellation intent is durable.
    StartAbort,
    AbortUnconfirmed,
    /// Trusted final/error/aborted event associated with the original run.
    Completed,
    Failed,
    Aborted,
    PreparationFailed,
    QueueExpired,
    Recover,
    BindContext,
    /// Carrier is permanently ended and proven never sent. Rebinding is a
    /// repository operation in the same transaction, not another network call.
    ReleaseContext,
    ConsumeContext,
    /// Carrier has not started I/O and its prepared payload will be invalidated.
    WithdrawBoundContext,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryContextAction {
    Keep,
    Release,
    Consume,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeliveryStateChange {
    pub state: MessageDeliveryState,
    pub changed: bool,
    pub release_active: bool,
    pub request_abort: bool,
    pub context_action: DeliveryContextAction,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DeliveryLifecycleError {
    #[error("delivery state version is stale: expected {expected}, actual {actual}")]
    StaleVersion { expected: u64, actual: u64 },
    #[error("invalid persisted delivery state")]
    InvalidState,
    #[error("event {event:?} is invalid for delivery status {status:?}")]
    InvalidTransition {
        status: MessageDeliveryStatus,
        event: DeliveryLifecycleEvent,
    },
    #[error("delivery state version exhausted")]
    VersionExhausted,
}

pub trait MessageDeliveryCoreService: Send + Sync {
    fn transition(
        &self,
        current: MessageDeliveryState,
        expected_state_version: u64,
        event: DeliveryLifecycleEvent,
    ) -> Result<DeliveryStateChange, DeliveryLifecycleError>;
}

/// A committed send row, scoped to one environment and target Bot by the
/// repository. Inject rows never enter this scheduling snapshot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryScheduleEntry {
    pub delivery_id: String,
    pub session_id: String,
    pub source_session_seq: u64,
    pub state: MessageDeliveryState,
    pub available_at_ms: i64,
    pub expire_at_ms: Option<i64>,
}

pub struct DeliveryScheduleSnapshot<'a> {
    pub entries: &'a [DeliveryScheduleEntry],
    pub max_running: usize,
    pub bot_online: bool,
    pub paused: bool,
    pub now_ms: i64,
    /// Monotonic elapsed milliseconds, not wall-clock Unix timestamps.
    pub monotonic_now_ms: u64,
    pub next_send_tick_ms: u64,
    /// The last session successfully authorized by send-start. Advance only
    /// after that transaction commits, never after merely preparing a payload.
    pub after_session_id: Option<&'a str>,
    pub preparing_delivery_ids: &'a std::collections::BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeliveryScheduleAction {
    Prepare {
        delivery_id: String,
        state_version: u64,
    },
    Expire {
        delivery_id: String,
        state_version: u64,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryScheduleDecision {
    pub action: Option<DeliveryScheduleAction>,
    pub waiting: Vec<(String, bcs_domain::message_delivery::DeliveryWaitReason)>,
}

pub trait MessageDeliverySchedulingCoreService: Send + Sync {
    /// Select at most one action. This is not an I/O authorization: the
    /// application rechecks a fresh snapshot after preparation, then commits
    /// send-start before calling a transport. The process owns the Bot rotation.
    fn select(
        &self,
        snapshot: DeliveryScheduleSnapshot<'_>,
    ) -> Result<DeliveryScheduleDecision, DeliveryLifecycleError>;
}
