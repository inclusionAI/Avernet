//! Recovery of absent original facts; no inference that an external send did not happen.
use serde::{Deserialize, Serialize};

/// Non-renewable preparation grace measured from the saved Run/Node start.
/// Expiry permits conditional revocation; it is not proof of process death.
pub const STATE_MACHINE_PREPARATION_GRACE_MS: u64 = 90_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineMissingStartupFact { Snapshot, Opening }

#[derive(Debug, Clone)]
pub struct FailStateMachineStartup {
    pub run_id: String,
    pub missing: StateMachineMissingStartupFact,
    pub failed_at_ms: u64,
    /// The foreground preparation error permits immediate failure. Recovery
    /// passes None and must wait for the original preparation grace to expire.
    pub preparation_error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineStartupFailure {
    pub run_id: String,
    pub group_id: String,
    pub session_id: String,
    pub session_activation_count: Option<i32>,
    pub run_created_at_ms: u64,
    pub failed_at_ms: u64,
    pub missing: StateMachineMissingStartupFact,
    pub error: String,
}
