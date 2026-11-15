//! Persisted reducer data shared by pure reduction and checkpoint repositories.
use crate::{Did, Tid, TimestampMillis};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunnerState {
    pub format_version: u32,
    pub value: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DeadlineKind {
    Task { tid: Tid },
    Dispatch { tid: Tid, did: Did },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Deadline {
    pub at: TimestampMillis,
    pub kind: DeadlineKind,
}
