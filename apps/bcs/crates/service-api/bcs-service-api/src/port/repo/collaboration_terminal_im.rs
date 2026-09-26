//! Terminal IM facts, separate from Run outcome and Session callback delivery.
use serde::{Deserialize, Serialize};
use crate::{StateMachineTerminalEvent, StateMachineTerminalNotification};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineTerminalImPayload {
    pub event: StateMachineTerminalEvent,
    pub session_activation_count: i32,
    pub notifications: Vec<StateMachineTerminalNotification>,
    pub created_at_ms: u64,
    pub deadline_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum StateMachineTerminalImDelivery {
    Pending { next_attempt_at_ms: u64, last_error: Option<String> },
    Sending,
    Delivered { provider_message_ref: Option<String> },
    Failed { error: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineTerminalImProgress {
    pub cleanup_completed: bool,
    pub deliveries: Vec<StateMachineTerminalImDelivery>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineTerminalImStatus { Pending, Delivered, Failed, Superseded }

#[derive(Debug, Clone)]
pub struct StateMachineTerminalImCheckpoint {
    pub payload: StateMachineTerminalImPayload,
    pub progress: StateMachineTerminalImProgress,
    pub status: StateMachineTerminalImStatus,
    pub lease_owner: Option<String>,
    pub lease_token: i64,
    pub lease_until_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct StateMachineTerminalImClaim {
    pub checkpoint: StateMachineTerminalImCheckpoint,
    pub owner: String,
    pub token: i64,
    pub lease_until_ms: u64,
}
