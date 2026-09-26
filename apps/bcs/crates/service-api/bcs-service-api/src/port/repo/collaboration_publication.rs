//! The synchronous Chat result publication barrier. No replay guarantee is
//! assumed for downstream routing; only a provably unsent Pending row may send.
use serde::{Deserialize, Serialize};
use crate::StateMachineResultPublishCommand;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineChatResultPayload {
    pub command: StateMachineResultPublishCommand,
    /// Bounds an ambiguous publisher call; recovery never extends this deadline.
    pub deadline_ms: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineChatResultStatus { Pending, Delivering, Delivered, Failed, Superseded }

#[derive(Debug, Clone)]
pub struct StateMachineChatResultCheckpoint {
    pub payload: StateMachineChatResultPayload,
    pub status: StateMachineChatResultStatus,
    pub lease_owner: Option<String>,
    pub lease_token: i64,
    pub lease_until_ms: Option<u64>,
    pub error: Option<String>,
    pub delivered_at_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct StateMachineChatResultClaim {
    pub payload: StateMachineChatResultPayload,
    pub owner: String,
    pub token: i64,
    pub lease_until_ms: u64,
}

#[derive(Debug, Clone)]
pub enum StateMachineChatResultOutcome { Published, Failed { error: String } }
