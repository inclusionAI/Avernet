use bcs_service_api::application::v1::{
    BotParticipantMode, CreateSession, Principal, SessionInput, SessionParticipantInput as ServiceParticipantInput,
    SessionStatus, UpdateSession,
};
use serde::Deserialize;

use super::group::deserialize_present_non_null;

fn default_limit() -> u64 {
    20
}

fn default_messages_limit() -> u64 {
    50
}

/// Optional task input for a session. If omitted on creation, the session
/// reuses the parent group's context as its task.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionInputDto {
    #[serde(default)]
    pub query: Option<String>,
}

impl From<SessionInputDto> for SessionInput {
    fn from(dto: SessionInputDto) -> Self {
        Self { query: dto.query }
    }
}

/// Input shape for a session participant on creation. Session participants
/// are Bot-only in V1; the facade resolves `bot_uuid` to a participant with
/// `actor_kind = Bot`.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionParticipantInput {
    pub bot_uuid: String,
    #[serde(default)]
    pub mode: Option<BotParticipantMode>,
}

impl From<SessionParticipantInput> for ServiceParticipantInput {
    fn from(dto: SessionParticipantInput) -> Self {
        Self {
            bot_uuid: dto.bot_uuid,
            mode: dto.mode,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateSessionRequest {
    pub driver_bot_uuid: String,
    pub participants: Vec<SessionParticipantInput>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub input: Option<SessionInputDto>,
}

impl CreateSessionRequest {
    pub fn into_command(self, principal: Principal, group_id: String) -> CreateSession {
        CreateSession {
            principal,
            group_id,
            driver_bot_uuid: self.driver_bot_uuid,
            title: self.title,
            input: self.input.map(SessionInput::from),
            participants: self
                .participants
                .into_iter()
                .map(ServiceParticipantInput::from)
                .collect(),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateSessionRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub title: Option<String>,
}

impl UpdateSessionRequest {
    pub fn into_command(self, principal: Principal, session_id: String) -> UpdateSession {
        UpdateSession {
            principal,
            session_id,
            title: self.title,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateSessionParticipantRequest {
    pub mode: BotParticipantMode,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListSessionsQuery {
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
    #[serde(default)]
    pub status: Option<SessionStatus>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListSessionMessagesQuery {
    /// Exclusive `created_at` cursor for cursor-based pagination. Omit on the
    /// first page; pass the response's `next_cursor` to fetch the next page.
    #[serde(default)]
    pub before: Option<u64>,
    #[serde(default = "default_messages_limit")]
    pub limit: u64,
}
