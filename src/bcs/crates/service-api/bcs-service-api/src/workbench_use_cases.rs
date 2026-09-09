//! Workbench session application service contracts.
//!
//! Delivery adapters should depend on these use-case contracts instead of
//! reaching into core group or registry traits directly.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::types::MessageViewScope;
use crate::{GroupUseCaseError, ParticipantKind, ParticipantMode, ServiceError};

#[derive(Debug, Clone)]
pub struct WorkbenchConnectCommand {
    /// Authenticated actor bound by the transport. For Workbench this is the
    /// Human principal and cannot be overridden by request params.
    pub bound_actor_id: Option<String>,
    /// Persisted participant view explicitly selected by the tab. Omission
    /// preserves the legacy unprojected Workbench connection; an explicit Bot
    /// must pass ownership checks.
    pub view_actor_id: Option<String>,
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

/// Authorize a Workbench Human to abort active runs for one Bot in one
/// canonical Session. The target Bot is never treated as the caller.
#[derive(Debug, Clone)]
pub struct WorkbenchChatAbortAuthorizationCommand {
    pub bound_actor_id: Option<String>,
    pub group_id: String,
    pub session_id: String,
    pub target_bot_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkbenchParticipantView {
    pub bot_uuid: String,
    pub role: String,
    #[serde(rename = "type")]
    pub kind: ParticipantKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<ParticipantMode>,
    pub message_view_scope: MessageViewScope,
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
    #[error("view actor must be the authenticated Human or one of their owned Bots")]
    ForbiddenViewActor,
    #[error("sender must be the current Human or a Bot owned by the current Human")]
    ForbiddenSender,
    #[error("participant mode is absent; switch to present before sending")]
    ParticipantAbsent,
    #[error("sender must be a participant of this group")]
    SenderNotInGroup,
    #[error("target Bot must be a participant of this Session")]
    TargetBotNotInSession,
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
            Self::ForbiddenViewActor => "forbidden_view_actor",
            Self::ForbiddenSender => "forbidden_sender",
            Self::ParticipantAbsent => "participant_absent",
            Self::SenderNotInGroup => "sender_not_in_group",
            Self::TargetBotNotInSession => "target_bot_not_in_session",
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

    async fn authorize_chat_abort(
        &self,
        _command: WorkbenchChatAbortAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        Err(WorkbenchUseCaseError::Service(
            ServiceError::InvalidOperation {
                message: "chat.abort authorization is not configured".to_string(),
                request_id: None,
            },
        ))
    }
}
