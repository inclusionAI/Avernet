use async_trait::async_trait;
use bcs_dist_model::*;
use futures::stream::BoxStream;
use crate::port::dist_output::FormattedOutputEvent;

pub use super::dist_admission::{GroupStartLookup, TaskAdmissionCommand as AdmitTaskCommand};

/// The input is admitted, whether by this call or an earlier one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoreAdmission(pub AdmissionReceipt);

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DistGatewayCoreError {
    #[error("task not found")]
    NotFound,
    #[error("scope or target mismatch")]
    ScopeMismatch,
    #[error("invalid task transition")]
    InvalidTransition,
    #[error("input content conflict")]
    ContentConflict,
    #[error("session quarantined")]
    Quarantined,
    #[error("dependency unavailable: {code}")]
    Unavailable { code: String },
    #[error("input outcome unknown")]
    OutcomeUnknown { input_id: InputId },
}

#[derive(Debug, Clone, PartialEq)]
pub struct RestoredTaskCut {
    pub view: TaskView,
    pub cursor: Option<TaskCursor>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum CoreFollowItem {
    Events {
        events: Vec<FormattedOutputEvent>,
        next: TaskCursor,
    },
    Gap,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CoreResumeCheck {
    Valid,
    Gap,
    InvalidScope,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoreTaskQuery {
    pub sid: Option<Sid>,
    pub statuses: Vec<TaskStatus>,
    pub after: Option<PageToken>,
    pub limit: std::num::NonZeroU32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CoreTaskPage {
    pub tasks: Vec<TaskView>,
    pub next: Option<PageToken>,
}

/// Validates resolved domain scope, frozen definitions and task transitions.
/// Application owns caller resolution, transport normalization and waiting.
#[async_trait]
pub trait DistGatewayCoreService: Send + Sync {
    /// Read the original strict receipt without authorizing any new work.
    async fn lookup_group_start(
        &self, _scope: &TaskScope, _group: &GroupId, _id: &InputId,
    ) -> Result<GroupStartLookup, DistGatewayCoreError> {
        Err(DistGatewayCoreError::Unavailable { code: "group_lookup_unconfigured".into() })
    }
    async fn admit(
        &self,
        scope: &TaskScope,
        command: AdmitTaskCommand,
    ) -> Result<CoreAdmission, DistGatewayCoreError>;
    /// The group a session was frozen for at admission. NotFound when the
    /// session is absent or belongs to another scope.
    async fn session_group(
        &self,
        scope: &TaskScope,
        sid: &Sid,
    ) -> Result<GroupId, DistGatewayCoreError>;
    async fn restore_task(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<RestoredTaskCut, DistGatewayCoreError>;
    async fn validate_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<CoreResumeCheck, DistGatewayCoreError>;
    fn follow_task(
        &self,
        scope: TaskScope,
        cursor: TaskCursor,
    ) -> BoxStream<'static, Result<CoreFollowItem, DistGatewayCoreError>>;
    async fn list_tasks(
        &self,
        scope: &TaskScope,
        query: CoreTaskQuery,
    ) -> Result<CoreTaskPage, DistGatewayCoreError>;
}
