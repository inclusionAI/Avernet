//! Stable task input intent. Runtime initialization belongs to Worker admission.
use crate::port::{
    dist_agent::FrozenSessionProposal,
    repo::distributed::{common::StoreError, inbox::AdmitOutcome},
};
use async_trait::async_trait;
use bcs_dist_model::*;

#[derive(Debug, Clone, PartialEq)]
pub struct TaskAdmissionCommand {
    pub input: InputEnvelope,
    /// A resolved definition is a proposal only until Inbox selects the original.
    pub create_session: Option<FrozenSessionProposal>,
    pub start_task: bool,
}

#[async_trait]
pub trait TaskAdmissionCoreService: Send + Sync {
    /// scope comes from authenticated caller context. Same scoped identity and
    /// caller content recovers the original initialization, even across races.
    /// Success confirms recoverable input, initial state and durable activation.
    async fn admit(
        &self,
        scope: &TaskScope,
        command: TaskAdmissionCommand,
    ) -> Result<AdmitOutcome, StoreError>;
}
