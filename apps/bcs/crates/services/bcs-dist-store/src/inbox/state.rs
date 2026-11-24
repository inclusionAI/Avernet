use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, inbox::AdmitRequest};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// One durable admission state machine keyed by `(scope, input_id)`.
///
/// The request and all repair progress live in this single document. There is
/// no separate intent or canonical record: retries read this document and
/// advance it with JSON CAS until a decision is durable.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct AdmissionRecord {
    pub schema_version: u32,
    pub generation: u64,
    pub request: AdmitRequest,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub connector: Option<bcs_service_api::port::repo::distributed::inbox::ConnectorEventIdentity>,
    /// Strict-group rounds stop before the Start feed entry until the
    /// checkpoint license is granted; generic entries never carry this mark.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub strict: Option<StrictRound>,
    pub created_at: TimestampMillis,
    pub position: Option<InboxPos>,
    pub decision: Option<Decision>,
    pub registered: bool,
    pub append_attempts: u64,
    pub append_credits: u64,
    pub phase: Phase,
}

/// The strict round identity bound to one admission record. The Start feed
/// entry and schedule activation are appended only when the checkpoint license
/// matches this exact request id, round generation and Tid.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StrictRound {
    pub request_id: String,
    pub generation: u64,
}
impl AdmissionRecord {
    pub fn start(&self) -> bool {
        self.request.create_task.is_some()
    }
    pub fn sid(&self) -> &Sid {
        &self.request.input.sid
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Phase {
    /// Admission document exists but schedule registration has not completed.
    #[serde(alias = "Selected", alias = "Released")]
    Created,
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
    ContentConflict,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct Slot {
    pub generation: u64,
    pub start: bool,
    pub decision: Option<Decision>,
    /// Set only for strict-group staged starts; generic entries are rejected
    /// for sessions holding either a staged strict slot or a strict license.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub strict: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct ClosedGate {
    pub operation: RequestId,
    pub generation: u64,
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
