use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, CreateSession, DeliveryType, IdentityPolicy,
    ParticipantMode, ParticipantRole, Principal, SessionCaller, SessionKind, SessionStatus,
    UpdateSession, select_principal,
};
use serde::Deserialize;
use serde_json::{Map, Value};

use super::group::deserialize_present_non_null;

fn default_limit() -> u64 {
    20
}

fn default_messages_limit() -> u64 {
    50
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum SessionInputDto {
    String(String),
    Object(Map<String, Value>),
}

impl From<SessionInputDto> for Value {
    fn from(dto: SessionInputDto) -> Self {
        match dto {
            SessionInputDto::String(value) => Self::String(value),
            SessionInputDto::Object(value) => Self::Object(value),
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CreatorRoleDto {
    Consultant,
    Manager,
    Worker,
    Observer,
}

impl From<CreatorRoleDto> for ParticipantRole {
    fn from(role: CreatorRoleDto) -> Self {
        match role {
            CreatorRoleDto::Consultant => Self::Consultant,
            CreatorRoleDto::Manager => Self::Manager,
            CreatorRoleDto::Worker => Self::Worker,
            CreatorRoleDto::Observer => Self::Observer,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateSessionRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub title: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub kind: Option<SessionKind>,
    /// Optional explicit creator Actor. The V1 wire name is retained even
    /// though an authenticated Human may pass its own `human_{user.id}`.
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub acting_bot_id: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub creator_role: Option<CreatorRoleDto>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub input: Option<SessionInputDto>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub meta: Option<Map<String, Value>>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub context_delivery: Option<DeliveryType>,
}

impl CreateSessionRequest {
    pub fn into_command(
        self,
        caller: &AuthenticatedCaller,
        group_id: String,
    ) -> Result<CreateSession, ApplicationError> {
        if self.acting_bot_id.as_deref() == Some("") {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "acting_bot_id must not be empty",
            ));
        }
        if let Some(meta) = self.meta.as_ref() {
            validate_session_metadata(meta)?;
        }
        let caller = match select_principal(caller, IdentityPolicy::HumanOrOwnedBot)? {
            Principal::Human(human) => SessionCaller::Human {
                actor_id: format!("human_{}", human.subject.id),
                owner_id: human.subject.id,
                display_name: human.subject.display_name.or(human.subject.full_name),
            },
            Principal::Bot(bot) => SessionCaller::Bot {
                bot_uuid: bot.bot_uuid,
            },
        };
        Ok(CreateSession {
            caller,
            group_id,
            title: self.title,
            kind: self.kind,
            acting_bot_id: self.acting_bot_id,
            creator_role: self.creator_role.map(ParticipantRole::from),
            input: self.input.map(Value::from),
            meta: self.meta.map(Value::Object),
            context_delivery: self.context_delivery,
        })
    }
}

fn validate_session_metadata(meta: &Map<String, Value>) -> Result<(), ApplicationError> {
    if let Some(callback_target) = open_object(meta, "callback_target")? {
        string_fields(
            callback_target,
            "meta.callback_target",
            &["baas_session_id", "user_id", "open_conversation_id"],
        )?;
    }
    if let Some(channel) = open_object(meta, "channel")? {
        string_fields(
            channel,
            "meta.channel",
            &[
                "source",
                "binding_id",
                "conversation_id",
                "conversation_type",
                "im_user_id",
            ],
        )?;
        enum_field(
            channel,
            "meta.channel",
            "session_scope",
            &["conversation", "per_sender"],
        )?;
        enum_field(
            channel,
            "meta.channel",
            "context_projection",
            &["group", "direct_bot"],
        )?;
    }
    enum_field(meta, "meta", "context_projection", &["group", "direct_bot"])
}

fn open_object<'a>(
    parent: &'a Map<String, Value>,
    field: &str,
) -> Result<Option<&'a Map<String, Value>>, ApplicationError> {
    match parent.get(field) {
        None => Ok(None),
        Some(Value::Object(value)) => Ok(Some(value)),
        Some(_) => Err(invalid_metadata(format!("meta.{field} must be an object"))),
    }
}

fn string_fields(
    parent: &Map<String, Value>,
    path: &str,
    fields: &[&str],
) -> Result<(), ApplicationError> {
    for field in fields {
        if let Some(value) = parent.get(*field)
            && !value.is_string()
        {
            return Err(invalid_metadata(format!("{path}.{field} must be a string")));
        }
    }
    Ok(())
}

fn enum_field(
    parent: &Map<String, Value>,
    path: &str,
    field: &str,
    accepted: &[&str],
) -> Result<(), ApplicationError> {
    let Some(value) = parent.get(field) else {
        return Ok(());
    };
    let Some(value) = value.as_str() else {
        return Err(invalid_metadata(format!("{path}.{field} must be a string")));
    };
    if !accepted.contains(&value) {
        return Err(invalid_metadata(format!(
            "{path}.{field} has an unsupported value"
        )));
    }
    Ok(())
}

fn invalid_metadata(message: String) -> ApplicationError {
    ApplicationError::invalid("invalid_request", message)
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
    pub mode: ParticipantMode,
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
pub struct CollectSessionRequest {
    pub participant: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UncollectSessionQuery {
    pub participant: String,
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
    /// Exclusive legacy-compatible millisecond timestamp bound.
    #[serde(default)]
    pub before: Option<u64>,
    #[serde(default = "default_messages_limit")]
    pub limit: u64,
    /// Optional viewer identity for message history visibility scoping.
    #[serde(default)]
    pub view_bot_id: Option<String>,
    /// Opt in to best-effort in-memory text/tool snapshots for active runs.
    #[serde(default)]
    pub include_pending: bool,
}
