use bcs_service_api::application::v1::{
    AuthenticatedCaller, BotParticipantMode, CreateSession, SessionInput, SessionStatus, UpdateSession,
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


#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateSessionRequest {
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub input: Option<SessionInputDto>,
}

impl CreateSessionRequest {
    pub fn into_command(self, caller: AuthenticatedCaller, group_id: String) -> CreateSession {
        CreateSession {
            caller,
            group_id,
            title: self.title,
            input: self.input.map(SessionInput::from),
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
    pub fn into_command(self, caller: AuthenticatedCaller, session_id: String) -> UpdateSession {
        UpdateSession {
            caller,
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
pub struct AddSessionParticipantRequest {
    pub bot_uuid: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeleteSessionQuery {
    #[serde(default)]
    pub acting_bot_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListSessionsQuery {
    #[serde(default)]
    pub view_bot_id: Option<String>,
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
    /// Opaque composite cursor for cursor-based pagination (VYQHI). Encoded
    /// as `"created_at:session_seq"`. Omit on the first page; pass the
    /// response's `next_cursor` to fetch the next page.
    #[serde(default)]
    pub before: Option<String>,
    #[serde(default = "default_messages_limit")]
    pub limit: u64,
    /// Optional viewer identity for message history visibility scoping.
    #[serde(default)]
    pub view_bot_id: Option<String>,
}
