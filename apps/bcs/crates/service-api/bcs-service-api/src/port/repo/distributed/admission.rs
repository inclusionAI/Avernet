//! Durable admission maintenance and gate controls. These are Worker repository
//! ports, never a second business submission API.
use super::{common::*, inbox::AdmitRequest};
use async_trait::async_trait;
use bcs_dist_model::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AdmissionClosure {
    pub sid: Sid,
    pub generation: u64,
    pub operation_id: RequestId,
    /// Fixed inclusive upper bound in this session's shard journal partition.
    pub journal_cut: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AdmissionObligation {
    pub request: AdmitRequest,
    pub accepted: bool,
    /// A registered creator may still arrive late. Complete a terminal task
    /// create-once record and publication before declaring closure complete.
    pub may_have_created_task: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateCloseOutcome {
    Closed(AdmissionClosure),
    Fenced,
    Conflict,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct AdmissionMaintenance {
    pub examined: u32,
    pub completed: u32,
    pub pending: u32,
    pub activated: u32,
    pub round_complete: bool,
    /// Continuous completion prefix, independent of the forward scan cursor.
    pub completed_through: u64,
}

#[async_trait]
pub trait AdmissionMaintenancePort: Send + Sync {
    /// Independently repairs durable intents, including crashes before canonical
    /// selection. Bounded work; one unresolved session cannot block other pages.
    async fn repair(&self, shard: ShardId) -> Result<AdmissionMaintenance, StoreError>;
    /// Own persistent cursor and fixed cut; completion of repair never removes
    /// historical sessions from discovery, including future deadlines.
    async fn discover(&self, shard: ShardId) -> Result<AdmissionMaintenance, StoreError>;
}

#[async_trait]
pub trait AdmissionControlPort: Send + Sync {
    async fn close_gate(
        &self,
        owner: &SessionOwnership,
        expected_generation: u64,
        operation_id: RequestId,
    ) -> Result<GateCloseOutcome, StoreError>;
    /// Includes released Accepted slots and creators paused before task creation.
    /// Closure still requires terminal archival/publication for every obligation.
    /// A bounded page may retain the same continuation while one durable
    /// admission phase advances; retry through the supervised maintenance queue.
    async fn closure_obligations(
        &self,
        closure: &AdmissionClosure,
        page: PageRequest,
    ) -> Result<Page<AdmissionObligation>, StoreError>;
}
