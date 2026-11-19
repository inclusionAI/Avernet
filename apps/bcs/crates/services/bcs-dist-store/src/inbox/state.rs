use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, inbox::AdmitRequest};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct Intent {
    pub schema_version: u32,
    pub sequence: u64,
    pub generation: u64,
    pub request: AdmitRequest,
    pub created_at: TimestampMillis,
}
impl Intent {
    pub fn start(&self) -> bool {
        self.request.create_task.is_some()
    }
    pub fn sid(&self) -> &Sid {
        &self.request.input.sid
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub(super) struct JournalRoot {
    pub next: u64,
    pub entries: u64,
    pub bytes: u64,
    pub normal_entries: u64,
    pub normal_bytes: u64,
    pub sessions: u64,
    pub pending: BTreeMap<u64, Intent>,
    pub inbox: BTreeMap<Sid, InboxBudget>,
    pub repair_health: u64,
    pub discovery_health: u64,
    pub repair_healthy_round: u64,
    pub discovery_healthy_round: u64,
    pub repair_health_age_millis: u64,
    pub discovery_health_age_millis: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub(super) struct InboxBudget {
    pub entries: u64,
    pub bytes: u64,
    pub normal_entries: u64,
    pub normal_bytes: u64,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Phase {
    Selected,
    Released,
    Registered,
    SpecReady,
    ScopeIndexed,
    SessionIndexed,
    TaskReady,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Decision {
    Accepted,
    Rejected,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct Canonical {
    pub schema_version: u32,
    pub intent: Intent,
    pub position: Option<InboxPos>,
    pub decision: Option<Decision>,
    pub registered: bool,
    pub append_attempts: u64,
    pub append_credits: u64,
    pub phase: Phase,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct Slot {
    pub generation: u64,
    pub sequence: u64,
    pub start: bool,
    pub decision: Option<Decision>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct ClosedGate {
    pub operation: RequestId,
    pub generation: u64,
    pub cut: Option<u64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct Schedule {
    pub schema_version: u32,
    pub generation: u64,
    /// Monotonic checkpoint control revision fences cross-document transitions.
    #[serde(default)]
    pub control_revision: u64,
    pub closed: Option<ClosedGate>,
    #[serde(default)]
    pub recovered: Option<bcs_service_api::port::repo::distributed::admission::GateRecoveryReceipt>,
    pub owner: Option<ShardOwner>,
    pub activation: u64,
    pub next_check: Option<TimestampMillis>,
    pub listening: bool,
    pub slots: BTreeMap<String, Slot>,
}
impl Default for Schedule {
    fn default() -> Self {
        Self {
            schema_version: 1,
            generation: 1,
            control_revision: 0,
            closed: None,
            recovered: None,
            owner: None,
            activation: 0,
            next_check: None,
            listening: false,
            slots: BTreeMap::new(),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub(super) struct Scan {
    pub cut: u64,
    pub cursor: u64,
    pub completed: u64,
    pub pending: BTreeSet<u64>,
    pub started_at: i64,
    pub round: u64,
    pub retry_after: u64,
    pub retry_first: bool,
}
