use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::group::{DeleteResult, Page};
use super::{ApplicationError, AuthenticatedCaller};
use crate::StateMachineRunView;

pub use bcs_domain::{ActorKind, ParticipantMode, ParticipantRole};
pub use crate::{DeliveryType, SessionCaller, SessionKind};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionStatus {
    Running,
    Completed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionParticipant {
    pub actor_id: String,
    pub actor_kind: ActorKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub role: ParticipantRole,
    pub mode: ParticipantMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub joined_at: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionSummary {
    pub session_id: String,
    pub version: i32,
    pub group_id: String,
    pub status: SessionStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub participant_count: Option<usize>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionDetail {
    pub session_id: String,
    pub version: i32,
    pub group_id: String,
    pub status: SessionStatus,
    pub kind: SessionKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub meta: Option<Value>,
    pub participants: Vec<SessionParticipant>,
    pub created_at: u64,
    pub updated_at: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state_machine_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state_machine_run: Option<StateMachineRunView>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionCompletionResult {
    pub session_id: String,
    pub status: SessionStatus,
    pub completed_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionCollectionResult {
    pub session_id: String,
    pub participant: String,
    pub collected: bool,
}

/// Input shape for a session participant on creation.
///
/// Session participants are Bot-only in V1; the facade resolves `bot_uuid`
/// to a `SessionParticipant` with `actor_kind = Bot`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionParticipantInput {
    pub bot_uuid: String,
}

#[derive(Debug, Clone)]
pub struct CreateSessionOutcome {
    pub session: SessionDetail,
    pub created: bool,
}

#[derive(Debug, Clone)]
pub struct CreateSession {
    pub caller: SessionCaller,
    pub group_id: String,
    pub title: Option<String>,
    pub kind: Option<SessionKind>,
    /// Optional explicit creator Actor ID supplied through V1
    /// `acting_bot_id`; Human callers may select themselves or an owned Bot.
    pub acting_bot_id: Option<String>,
    pub creator_role: Option<ParticipantRole>,
    pub input: Option<Value>,
    pub meta: Option<Value>,
    pub context_delivery: Option<DeliveryType>,
}

#[derive(Debug, Clone)]
pub struct ListSessions {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
    pub view_bot_id: Option<String>,
    pub offset: u64,
    pub limit: u64,
    pub status: Option<SessionStatus>,
}

#[derive(Debug, Clone)]
pub struct GetSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
}

#[derive(Debug, Clone)]
pub struct UpdateSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub title: Option<String>,
}

#[derive(Debug, Clone)]
pub struct DeleteSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub acting_bot_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CompleteSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
}

#[derive(Debug, Clone)]
pub struct CollectSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub participant: String,
}

#[derive(Debug, Clone)]
pub struct UncollectSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub participant: String,
}

#[derive(Debug, Clone)]
pub struct AddSessionParticipant {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub bot_uuid: String,
}

#[derive(Debug, Clone)]
pub struct UpdateSessionParticipant {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub bot_uuid: String,
    pub mode: ParticipantMode,
}

#[derive(Debug, Clone)]
pub struct DeleteSessionParticipant {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub bot_uuid: String,
}

/// Transport-independent session use cases for BCN OpenAPI v1.
///
/// Delivery adapters translate HTTP requests into these commands. The trait
/// is object-safe so an `Arc<dyn SessionService>` can be shared across routes.
#[async_trait]
pub trait SessionService: Send + Sync {
    async fn create(
        &self,
        command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError>;

    async fn list(&self, command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError>;

    async fn get(&self, query: GetSession) -> Result<SessionDetail, ApplicationError>;

    async fn update(&self, command: UpdateSession) -> Result<SessionDetail, ApplicationError>;

    async fn delete(&self, command: DeleteSession) -> Result<DeleteResult, ApplicationError>;

    async fn complete(
        &self,
        command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError>;

    async fn collect(
        &self,
        command: CollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError>;

    async fn uncollect(
        &self,
        command: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError>;

    async fn add_participant(
        &self,
        command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError>;

    async fn update_participant(
        &self,
        command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError>;

    async fn delete_participant(
        &self,
        command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError>;
}
