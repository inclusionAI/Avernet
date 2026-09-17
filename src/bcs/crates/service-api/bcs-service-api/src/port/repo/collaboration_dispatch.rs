//! Persisted State Machine Bot dispatch facts; no credentials or arbitrary URLs.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum StateMachineDispatchTarget {
    WebSocket,
    HttpProvider { provider_id: String, provider_bot_ref: String, protocol_version: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineDispatchPayload {
    pub run_id: String,
    pub node_id: String,
    pub attempt: i32,
    pub group_id: String,
    pub session_id: String,
    pub assignee_bot_id: String,
    pub delivery_request_id: String,
    pub target: StateMachineDispatchTarget,
    /// Already rendered request; credentials and forwarding headers are excluded.
    pub request: serde_json::Value,
    pub started_at_ms: u64,
    /// Original Node deadline, or original Provider timeout when Node timeout is disabled.
    /// This fallback bounds dispatch ambiguity only; it does not enable Node timeouts.
    pub deadline_ms: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineDispatchStatus { Pending, Delivering, Delivered, Failed, Superseded }

#[derive(Debug, Clone)]
pub struct StateMachineDispatchCheckpoint {
    pub payload: StateMachineDispatchPayload,
    pub status: StateMachineDispatchStatus,
    pub lease_owner: Option<String>,
    pub lease_token: i64,
    pub lease_until_ms: Option<u64>,
    pub error: Option<String>,
    pub delivered_at_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct StateMachineDispatchClaim {
    pub payload: StateMachineDispatchPayload,
    pub owner: String,
    pub token: i64,
    pub lease_until_ms: u64,
}

#[derive(Debug, Clone)]
pub enum StateMachineDispatchResult { Accepted, Rejected { error: String } }
