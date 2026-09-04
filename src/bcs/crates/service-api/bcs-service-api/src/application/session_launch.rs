//! Transport-neutral Session launch orchestration contract.
//!
//! HTTP adapters authenticate callers and translate their protocol-native
//! request fields into these commands. Implementations own resource
//! authorization, participant construction, persistence orchestration, and
//! collaboration startup.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{
    CollaborationRuntimeError, DeliveryType, ParticipantRole, ServiceError, Session, SessionKind,
    StateMachineRunView,
};

/// Authenticated caller identity normalized by a delivery adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionCaller {
    Human {
        actor_id: String,
        /// Persisted Bot ownership key for this Human.
        owner_id: String,
        display_name: Option<String>,
    },
    Bot {
        bot_uuid: String,
    },
}

/// Creator role supplied by an adapter. Unknown legacy values are retained so
/// the shared service can preserve the legacy public-vs-private validation rule.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RequestedSessionRole {
    Known(ParticipantRole),
    Unknown(String),
}

impl From<ParticipantRole> for RequestedSessionRole {
    fn from(role: ParticipantRole) -> Self {
        Self::Known(role)
    }
}

impl SessionCaller {
    pub fn actor_id(&self) -> &str {
        match self {
            Self::Human { actor_id, .. } => actor_id,
            Self::Bot { bot_uuid } => bot_uuid,
        }
    }

    pub fn owner_id(&self) -> Option<&str> {
        match self {
            Self::Human { owner_id, .. } => Some(owner_id),
            Self::Bot { .. } => None,
        }
    }

    pub fn display_name(&self) -> Option<&str> {
        match self {
            Self::Human { display_name, .. } => display_name.as_deref(),
            Self::Bot { .. } => None,
        }
    }
}

/// Fields shared by new-Session creation and legacy reactivation.
#[derive(Debug, Clone)]
pub struct SessionLaunchRequest {
    pub caller: SessionCaller,
    pub group_id: String,
    /// Explicit creator requested by the protocol, or `None` to use caller.
    pub requested_creator: Option<String>,
    pub title: Option<String>,
    /// `None` retains legacy Group-strategy-based defaulting.
    pub kind: Option<SessionKind>,
    /// Raw caller input. The application does not remap object fields.
    pub input: Option<Value>,
    /// Raw Session metadata. The application does not derive nested fields.
    pub meta: Option<Value>,
    /// Role for a public-Group creator who is not already a participant.
    pub public_creator_role: Option<RequestedSessionRole>,
    pub context_delivery: Option<DeliveryType>,
}

#[derive(Debug, Clone)]
pub struct CreateSessionLaunch {
    pub request: SessionLaunchRequest,
}

#[derive(Debug, Clone)]
pub struct ReactivateSessionLaunch {
    pub session_id: String,
    pub request: SessionLaunchRequest,
}

/// Bot run started by the context delivery for a newly-created Session.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InitialSessionRun {
    pub run_id: String,
    pub bot_uuid: String,
    pub activity_kind: InitialSessionRunActivityKind,
    pub state: InitialSessionRunState,
    pub started_at: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InitialSessionRunActivityKind {
    SessionContext,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InitialSessionRunState {
    Running,
    Failed,
}

#[derive(Debug, Clone)]
pub struct SessionLaunchOutcome {
    pub session: Session,
    /// Preserves the legacy create-or-reactivate result for adapter-specific status codes.
    pub created: bool,
    pub state_machine_run: Option<StateMachineRunView>,
    /// Context `chat.send` dispatched for a newly-created non-state-machine Session.
    pub initial_run: Option<InitialSessionRun>,
}

#[derive(Debug, thiserror::Error)]
pub enum SessionLaunchError {
    #[error("group not found: {0}")]
    GroupNotFound(String),
    #[error("session not found: {0}")]
    SessionNotFound(String),
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("invalid role: {0}")]
    InvalidRole(String),
    #[error("invalid request: {0}")]
    InvalidRequest(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("session callback pending: {0}")]
    CallbackPending(String),
    #[error(transparent)]
    Runtime(#[from] CollaborationRuntimeError),
    #[error(transparent)]
    Internal(#[from] ServiceError),
}

#[async_trait]
pub trait SessionLaunchService: Send + Sync {
    async fn create(
        &self,
        command: CreateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError>;

    async fn reactivate(
        &self,
        command: ReactivateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError>;
}
