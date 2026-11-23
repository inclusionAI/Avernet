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
    /// Compatibility field retained for wire stability. The MVP has no
    /// shard-wide admission journal, so this is always zero.
    pub journal_cut: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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
    /// Compatibility maintenance hook. The MVP does not promise recovery for
    /// an admission document that was never created; callers retry the same
    /// scoped InputId instead.
    async fn repair(&self, shard: ShardId) -> Result<AdmissionMaintenance, StoreError>;
    /// Compatibility maintenance hook for worker health reporting. Session
    /// discovery comes from the durable active index and schedule/checkpoint
    /// state, not from an admission journal.
    async fn discover(&self, shard: ShardId) -> Result<AdmissionMaintenance, StoreError>;
}

#[async_trait]
pub trait AdmissionControlPort: Send + Sync {
    async fn gate_state(&self, owner: &SessionOwnership) -> Result<AdmissionGateState, StoreError>;
    async fn close_gate(
        &self,
        guard: super::control::ControlGuard,
        expected_generation: u64,
        operation_id: RequestId,
    ) -> Result<GateCloseOutcome, StoreError>;
    /// Returns closure obligations derived from the Session-local schedule and
    /// checkpoint state. It does not scan a shard-wide admission journal.
    async fn closure_obligations(
        &self,
        closure: &AdmissionClosure,
        page: PageRequest,
    ) -> Result<Page<AdmissionObligation>, StoreError>;
}

/// Created by a complete fixed-cut closure scan, after every relevant task's
/// immutable archive and terminal publication have been independently verified.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClosureCertificate {
    pub closure: AdmissionClosure,
    pub completed_through: u64,
    pub terminal_tasks: u64,
    pub evidence_digest: Digest,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GateRecoveryReceipt {
    pub sid: Sid,
    pub isolation_id: RequestId,
    pub request_id: RequestId,
    pub owner: SessionOwnership,
    pub head: CommitSeq,
    pub control_revision: u64,
    pub closed_generation: u64,
    pub opened_generation: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TerminalTaskEvidence {
    pub scope: TaskScope,
    pub sid: Sid,
    pub tid: Tid,
    pub terminal_event: EventId,
    pub source_commit: CommitSeq,
    pub result_ref: BlobRef,
    /// Digest of the complete verified immutable FinalTaskRecord.
    pub archive_digest: Digest,
    /// Digest of durable append/publication completion facts and snapshot.
    pub publication_digest: Digest,
}
#[async_trait]
pub trait TerminalTaskEvidencePort: Send + Sync {
    /// Return Some only after reading actual scoped terminal TaskSnapshot,
    /// verifying its immutable archive and terminal append/publication completion.
    /// Missing/unpublished facts return None; corrupt/conflicting facts are errors.
    async fn terminal_evidence(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<TerminalTaskEvidence>, StoreError>;
}
#[derive(Debug, Clone, PartialEq)]
pub struct ClosureWork {
    pub closure: AdmissionClosure,
    pub completed_through: u64,
    pub obligation: Option<AdmissionObligation>,
    pub certificate: Option<ClosureCertificate>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateReopenOutcome {
    Opened(GateRecoveryReceipt),
    AlreadyOpened(GateRecoveryReceipt),
    Fenced,
    Conflict,
    NotReady,
}
#[async_trait]
pub trait AdmissionClosurePort: AdmissionControlPort {
    /// Advances provider-owned Session-local closure coverage. A pending Start
    /// remains pending until TerminalTaskEvidencePort verifies actual evidence.
    async fn advance_closure(
        &self,
        owner: &SessionOwnership,
        closure: &AdmissionClosure,
    ) -> Result<ClosureWork, StoreError>;
    /// Requires the exact current Recovering control state and completed local
    /// closure coverage. Reopens once and records the authority gate receipt.
    async fn reopen_gate(
        &self,
        guard: super::control::ControlGuard,
        request_id: RequestId,
    ) -> Result<GateReopenOutcome, StoreError>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdmissionGateState {
    pub generation: u64,
    pub closed_operation: Option<RequestId>,
    pub closure: Option<AdmissionClosure>,
}
