use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::group::{DeleteResult, Page};
use super::{ApplicationError, Principal};

pub use bcs_domain::{ActorKind, ParticipantMode, ParticipantRole};

/// Per-session bot collaboration mode for *client input* only.
///
/// V1 session participants can be Bots (added by clients via
/// `create` / `add_participant` / `update_participant`) or Humans (added by
/// the legacy invitation-accept path, `join_session_by_invite`, with
/// `actor_kind: Human, mode: Present`). The V1 `SessionParticipant` *output*
/// therefore carries the full domain `ParticipantMode` (4 values) so a Human
/// participant is surfaced verbatim. Client *input* still admits only the two
/// Bot-valid variants (`auto`, `muted`); Humans never enter via client input.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotParticipantMode {
    Auto,
    Muted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionStatus {
    Running,
    Completed,
}

/// Optional task input for a session. If omitted on creation, the session
/// reuses the parent group's context as its task.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct SessionInput {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub query: Option<String>,
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionDetail {
    pub session_id: String,
    pub version: i32,
    pub group_id: String,
    pub status: SessionStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input: Option<SessionInput>,
    pub participants: Vec<SessionParticipant>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionCompletionResult {
    pub session_id: String,
    pub status: SessionStatus,
    pub completed_at: u64,
}

/// Input shape for a session participant on creation.
///
/// Session participants are Bot-only in V1; the facade resolves `bot_uuid`
/// to a `SessionParticipant` with `actor_kind = Bot`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionParticipantInput {
    pub bot_uuid: String,
    pub mode: Option<BotParticipantMode>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateSessionOutcome {
    pub session: SessionDetail,
    pub created: bool,
}

#[derive(Debug, Clone)]
pub struct CreateSession {
    pub principal: Principal,
    pub group_id: String,
    pub driver_bot_uuid: String,
    pub title: Option<String>,
    pub input: Option<SessionInput>,
    pub participants: Vec<SessionParticipantInput>,
}

#[derive(Debug, Clone)]
pub struct ListSessions {
    pub principal: Principal,
    pub group_id: String,
    pub offset: u64,
    pub limit: u64,
    pub status: Option<SessionStatus>,
}

#[derive(Debug, Clone)]
pub struct GetSession {
    pub principal: Principal,
    pub session_id: String,
}

#[derive(Debug, Clone)]
pub struct UpdateSession {
    pub principal: Principal,
    pub session_id: String,
    pub title: Option<String>,
}

#[derive(Debug, Clone)]
pub struct DeleteSession {
    pub principal: Principal,
    pub session_id: String,
}

#[derive(Debug, Clone)]
pub struct CompleteSession {
    pub principal: Principal,
    pub session_id: String,
}

#[derive(Debug, Clone)]
pub struct AddSessionParticipant {
    pub principal: Principal,
    pub session_id: String,
    pub bot_uuid: String,
    pub mode: Option<BotParticipantMode>,
}

#[derive(Debug, Clone)]
pub struct UpdateSessionParticipant {
    pub principal: Principal,
    pub session_id: String,
    pub bot_uuid: String,
    pub mode: BotParticipantMode,
}

#[derive(Debug, Clone)]
pub struct DeleteSessionParticipant {
    pub principal: Principal,
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
