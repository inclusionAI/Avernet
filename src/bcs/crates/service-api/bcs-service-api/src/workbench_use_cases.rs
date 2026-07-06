//! Workbench session application service contracts.
//!
//! Delivery adapters should depend on these use-case contracts instead of
//! reaching into core group or registry traits directly.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::{GroupUseCaseError, ParticipantKind, ParticipantMode, ServiceError};

#[derive(Debug, Clone)]
pub struct WorkbenchConnectCommand {
    pub bound_actor_id: Option<String>,
    pub group_id: String,
    pub session_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct WorkbenchChatAuthorizationCommand {
    pub bound_actor_id: Option<String>,
    pub group_id: String,
    pub from_actor_id: String,
    pub session_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkbenchParticipantView {
    pub bot_uuid: String,
    pub role: String,
    #[serde(rename = "type")]
    pub kind: ParticipantKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<ParticipantMode>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkbenchConnectOutcome {
    pub group_id: String,
    pub participants: Vec<WorkbenchParticipantView>,
}

#[derive(Debug, thiserror::Error)]
pub enum WorkbenchUseCaseError {
    #[error("valid Human cookie is required for this Workbench request")]
    Unauthorized,
    #[error("current Human is not a participant and owns no Bot in this group")]
    ForbiddenGroupAccess,
    #[error("sender must be the current Human or a Bot owned by the current Human")]
    ForbiddenSender,
    #[error("participant mode is absent; switch to present before sending")]
    ParticipantAbsent,
    #[error("sender must be a participant of this group")]
    SenderNotInGroup,
    #[error("Group not found: {0}")]
    GroupNotFound(String),
    #[error(transparent)]
    Group(GroupUseCaseError),
    #[error(transparent)]
    Service(ServiceError),
}

impl WorkbenchUseCaseError {
    pub fn from_service_error(error: ServiceError) -> Self {
        match error {
            ServiceError::GroupNotFound(group_id) => Self::GroupNotFound(group_id),
            ServiceError::Unauthorized(_) => Self::Unauthorized,
            other => Self::Service(other),
        }
    }

    pub fn from_group_error(error: GroupUseCaseError) -> Self {
        match error {
            GroupUseCaseError::Service(service_error) => Self::from_service_error(service_error),
            GroupUseCaseError::Unauthorized(_) => Self::Unauthorized,
            other => Self::Group(other),
        }
    }

    pub fn code(&self) -> &'static str {
        match self {
            Self::Unauthorized => "unauthorized",
            Self::ForbiddenGroupAccess => "forbidden_group_access",
            Self::ForbiddenSender => "forbidden_sender",
            Self::ParticipantAbsent => "participant_absent",
            Self::SenderNotInGroup => "sender_not_in_group",
            Self::GroupNotFound(_) => "group_not_found",
            Self::Group(_) | Self::Service(_) => "internal_error",
        }
    }

    pub fn message(&self) -> String {
        self.to_string()
    }
}

#[async_trait]
pub trait WorkbenchSessionService: Send + Sync {
    async fn connect(
        &self,
        command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError>;

    async fn authorize_chat_send(
        &self,
        command: WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError>;
}
