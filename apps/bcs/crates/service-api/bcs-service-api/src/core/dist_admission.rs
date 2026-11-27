//! Stable task input intent. Runtime initialization belongs to Worker admission.
use crate::port::{
    dist_agent::FrozenGroupDefinition,
    repo::distributed::{common::StoreError, inbox::AdmitOutcome},
};
use async_trait::async_trait;
use bcs_dist_model::*;

#[derive(Debug, Clone, PartialEq)]
pub struct TaskAdmissionCommand {
    pub input: InputEnvelope,
    /// A resolved definition is a proposal only until Inbox selects the original.
    pub create_session: Option<FrozenGroupDefinition>,
    pub start_task: bool,
}

/// Observational classification. Prepared is never a receipt or permission to execute.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GroupStartLookup {
    Absent,
    Prepared,
    Accepted(AdmissionReceipt),
    Expired,
}

#[async_trait]
pub trait TaskAdmissionCoreService: Send + Sync {
    /// Read the strict registry only; never reserve, repair or enqueue work.
    async fn lookup_group_start(
        &self, _scope: &TaskScope, _group: &GroupId, _id: &InputId,
    ) -> Result<GroupStartLookup, StoreError> {
        Err(StoreError::NotApplied { code: "group_lookup_unconfigured".into() })
    }

    /// scope comes from authenticated caller context. Same scoped identity and
    /// caller content recovers the original initialization, even across races.
    /// Success confirms recoverable input, initial state and durable activation.
    async fn admit(
        &self,
        scope: &TaskScope,
        command: TaskAdmissionCommand,
    ) -> Result<AdmitOutcome, StoreError>;
}
