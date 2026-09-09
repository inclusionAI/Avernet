//! Message / task / audit pure domain types.

use crate::{AttachmentType, MessageViewScope};
use serde::{Deserialize, Serialize};

/// Business producer domain used to select the server-side projection.
///
/// This value is independent from the containing Group strategy so a
/// one-shot StateMachine run inside a Chat Group cannot inherit Chat's legacy
/// projection semantics.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageVisibilityDomain {
    Chat,
    ManagerWorker,
    StateMachine,
}

/// Audience declared by ManagerWorker and StateMachine producers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum MessageAudience {
    Public,
    Directed { actor_ids: Vec<String> },
    FullOnly,
}

impl MessageAudience {
    pub fn directed(
        actor_ids: impl IntoIterator<Item = impl Into<String>>,
    ) -> Result<Self, &'static str> {
        let mut actor_ids = actor_ids
            .into_iter()
            .map(Into::into)
            .filter(|actor_id| !actor_id.is_empty())
            .collect::<Vec<_>>();
        actor_ids.sort();
        actor_ids.dedup();
        if actor_ids.is_empty() {
            return Err("directed audience must contain at least one actor id");
        }
        Ok(Self::Directed { actor_ids })
    }

    pub fn validate(&self) -> Result<(), &'static str> {
        match self {
            Self::Directed { actor_ids }
                if actor_ids.is_empty() || actor_ids.iter().any(String::is_empty) =>
            {
                Err("directed audience must contain non-empty actor ids")
            }
            Self::Directed { actor_ids } => {
                let mut normalized = actor_ids.clone();
                normalized.sort();
                normalized.dedup();
                if normalized.len() == actor_ids.len() {
                    Ok(())
                } else {
                    Err("directed audience actor ids must be unique")
                }
            }
            Self::Public | Self::FullOnly => Ok(()),
        }
    }

    pub fn contains(&self, actor_id: &str) -> bool {
        matches!(self, Self::Directed { actor_ids } if actor_ids.iter().any(|id| id == actor_id))
    }
}

/// Authenticated Human projection context applied by message repositories
/// before pagination. Bot views keep using the legacy owner filter and pass
/// no Human view context.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HumanMessageView {
    pub actor_id: String,
    pub scope: MessageViewScope,
    /// Legacy unclassified rows may be treated as ordinary Chat only when the
    /// containing Group is Chat. Scoped domains otherwise fail closed.
    pub allow_legacy_unclassified_chat: bool,
}

impl HumanMessageView {
    pub fn allows_artifact(
        &self,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
    ) -> bool {
        if self.scope == MessageViewScope::Full {
            return true;
        }
        match visibility_domain {
            MessageVisibilityDomain::Chat => true,
            MessageVisibilityDomain::ManagerWorker | MessageVisibilityDomain::StateMachine => {
                match audience {
                    Some(MessageAudience::Public) => true,
                    Some(audience @ MessageAudience::Directed { .. }) => {
                        audience.contains(&self.actor_id)
                    }
                    Some(MessageAudience::FullOnly) | None => false,
                }
            }
        }
    }

    pub fn allows(&self, message: &PersistedMessage) -> bool {
        if self.scope == MessageViewScope::Full {
            return true;
        }
        match message.visibility_domain {
            Some(MessageVisibilityDomain::Chat) => true,
            None => self.allow_legacy_unclassified_chat,
            Some(domain) => self.allows_artifact(domain, message.audience.as_ref()),
        }
    }
}

pub const BCS_STATE_MACHINE_MESSAGE_SENDER: &str = "bcs_state_machine";
pub const BCS_STATE_MACHINE_MESSAGE_SENDER_NAME: &str = "BCS State Machine";
pub const STATE_MACHINE_PANEL_MESSAGE_TYPE: &str = "state_machine_panel";
pub const STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE: &str =
    "state_machine_human_input_prompt";
pub const STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE: &str =
    "state_machine_human_input_response";
pub const BCS_SESSION_OPENING_MESSAGE_SENDER: &str = "bcs_opening_message";
pub const BCS_SESSION_OPENING_MESSAGE_SENDER_NAME: &str = "BCS";
pub const SESSION_OPENING_MESSAGE_TYPE: &str = "opening_message";

/// A task in the workspace.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    /// Task ID.
    pub id: String,
    /// Task description.
    pub description: String,
    /// Assigned bot (optional).
    #[serde(default)]
    pub assigned_to: Option<String>,
    /// Task status.
    pub status: TaskStatus,
}

/// Task status.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum TaskStatus {
    #[default]
    Pending,
    InProgress,
    Completed,
    Blocked,
}

/// Audit log entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    /// Timestamp.
    pub timestamp: u64,
    /// Action type.
    pub action: String,
    /// Actor (bot or user).
    pub actor: String,
    /// Details.
    #[serde(default)]
    pub details: String,
}

/// Attachment view for history echo: stable_metadata + a short-lived download
/// `url` minted at read time.
///
/// `expires_at` is unix **seconds** (matches share-token `exp` and
/// `share_consume`'s `as_secs()` check), NOT milliseconds.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageAttachment {
    pub attachment_id: String,
    #[serde(rename = "type")]
    pub attachment_type: AttachmentType,
    pub file_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mime_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    /// Short-lived share_url minted at history-read time; `<img src>` ready.
    /// `None` when the file was deleted / doesn't belong to the session / storage error.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    /// share_url expiry, unix seconds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<u64>,
}

/// A message in a group session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupMessage {
    /// Message ID.
    pub id: String,
    /// Timestamp (milliseconds since epoch).
    pub timestamp: u64,
    /// Sender (user id, bot id, or "system").
    pub sender: String,
    /// Message content.
    pub content: String,
    /// Message type.
    #[serde(default)]
    pub message_type: GroupMessageType,
    /// Bot display name (populated when message_type is Bot, stripped from [from:botName] prefix).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_name: Option<String>,
    /// Message role (user, tool_result, assistant).
    #[serde(default)]
    pub role: MessageRole,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub run_id: String,
    /// Optional history metadata from OpenClaw (assistantAggregation, plugin, etc.).
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        rename = "historyMeta"
    )]
    pub history_meta: Option<serde_json::Value>,
    /// Optional metadata (tool execution info, etc.).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata: Option<serde_json::Value>,
    /// Attachments carried by the message (images etc.). Omitted when `None`
    /// so older frontends that only read `content` are unaffected.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attachments: Option<Vec<MessageAttachment>>,
}

/// Role of a message sender.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    #[default]
    User,
    ToolResult,
    Assistant,
    System,
}

/// Type of group message.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum GroupMessageType {
    #[default]
    Bot,
    System,
    Fusion,
}

/// Delivery type for a routing target.
///
/// Determines how the message should be delivered to the bot:
/// - Send: Bot should respond (mentioned or coordinator for non-@ messages)
/// - Inject: Bot should observe silently
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DeliveryType {
    /// Bot should respond to this message.
    Send,
    /// Bot should observe silently (injected context only).
    Inject,
}

impl Default for DeliveryType {
    fn default() -> Self {
        Self::Inject
    }
}

/// Sender type for persisted messages.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SenderType {
    Bot,
    Human,
    System,
}

/// Message status for persisted messages.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum PersistedMessageStatus {
    #[default]
    Normal,
    Recalled,
    Deleted,
}

/// A message persisted in bcs_messages table.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersistedMessage {
    pub message_id: String,
    pub group_id: String,
    pub session_id: String,
    pub session_seq: i64,
    pub sender_id: String,
    pub sender_type: SenderType,
    pub message_type: String,
    pub content: serde_json::Value,
    pub client_msg_id: Option<String>,
    #[serde(default)]
    pub owner_bot_id: Option<String>,
    /// `None` only for legacy rows created before Domain/Audience persistence.
    #[serde(default)]
    pub visibility_domain: Option<MessageVisibilityDomain>,
    /// Required for ManagerWorker/StateMachine rows in visibility version 1.
    #[serde(default)]
    pub audience: Option<MessageAudience>,
    pub status: PersistedMessageStatus,
    pub created_at: u64,
    #[serde(default)]
    pub run_id: String,
}

/// Input for appending a new message.
#[derive(Debug, Clone)]
pub struct NewMessage {
    pub group_id: String,
    pub session_id: String,
    pub sender_id: String,
    pub sender_type: SenderType,
    pub message_type: String,
    pub content: serde_json::Value,
    pub client_msg_id: Option<String>,
    pub owner_bot_id: Option<String>,
    pub visibility_domain: MessageVisibilityDomain,
    pub audience: Option<MessageAudience>,
    pub created_at: u64,
    pub run_id: String,
}

/// Owner filter for message retrieval.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MessageOwnerFilter {
    Any,
    IsNull,
    Eq(String),
    /// `owner_bot_id IS NULL OR owner_bot_id = <viewer>` — 公共消息 + 发给该
    /// viewer 的系统消息副本。历史查询按 `view_bot_id` 回放"公共 + 自己的
    /// 系统副本"，收窄的仅是他人新增的私有副本，是旧 `Any`/`IsNull` 的超集。
    PublicOrOwner(String),
}

impl Default for MessageOwnerFilter {
    fn default() -> Self {
        Self::Any
    }
}

/// Query parameters for message retrieval.
#[derive(Debug, Clone)]
pub struct MessageQuery {
    pub group_id: String,
    pub session_id: String,
    pub cursor: Option<u64>,
    pub limit: u32,
    pub keyword: Option<String>,
    pub sender_id: Option<String>,
    pub message_type: Option<String>,
    pub owner_filter: MessageOwnerFilter,
    pub time_range: Option<(u64, u64)>,
    pub visible_from_seq: Option<i64>,
    pub human_view: Option<HumanMessageView>,
}

/// Paginated message result page.
///
/// `next_cursor` is a composite `(created_at, session_seq)` tuple so that
/// messages sharing a `created_at` at a page boundary are not permanently
/// skipped on the next page (VYQHI). Legacy `query_messages` callers that
/// surface only a `created_at` cursor to clients extract `.0` at the
/// application/HTTP boundary; the V1 `list_session_history` path encodes the
/// full tuple into an opaque string cursor.
#[derive(Debug, Clone)]
pub struct MessagePage {
    pub messages: Vec<PersistedMessage>,
    pub next_cursor: Option<(u64, i64)>,
    pub has_more: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn participant_view(actor_id: &str) -> HumanMessageView {
        HumanMessageView {
            actor_id: actor_id.to_string(),
            scope: MessageViewScope::Participant,
            allow_legacy_unclassified_chat: false,
        }
    }

    #[test]
    fn directed_audience_is_normalized_and_requires_an_actor() {
        assert_eq!(
            MessageAudience::directed(["human_b", "human_a", "human_b"]).unwrap(),
            MessageAudience::Directed {
                actor_ids: vec!["human_a".to_string(), "human_b".to_string()]
            }
        );
        assert!(MessageAudience::directed([""]).is_err());
    }

    #[test]
    fn participant_view_applies_domain_and_audience_matrix() {
        let view = participant_view("human_a");

        assert!(view.allows_artifact(MessageVisibilityDomain::Chat, None));
        assert!(view.allows_artifact(
            MessageVisibilityDomain::ManagerWorker,
            Some(&MessageAudience::Public),
        ));
        assert!(
            view.allows_artifact(
                MessageVisibilityDomain::StateMachine,
                Some(
                    &MessageAudience::directed(["human_a", "human_b"])
                        .expect("valid directed audience"),
                ),
            )
        );
        assert!(!view.allows_artifact(
            MessageVisibilityDomain::ManagerWorker,
            Some(&MessageAudience::directed(["human_b"]).expect("valid directed audience"),),
        ));
        assert!(!view.allows_artifact(
            MessageVisibilityDomain::StateMachine,
            Some(&MessageAudience::FullOnly),
        ));
        assert!(!view.allows_artifact(MessageVisibilityDomain::StateMachine, None));
    }

    #[test]
    fn group_message_omits_attachments_when_none() {
        let msg = GroupMessage {
            id: "m1".into(),
            timestamp: 1,
            sender: "s".into(),
            content: "hi".into(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: None,
            attachments: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(!json.contains("attachments"), "attachments must be omitted when None: {json}");
    }

    #[test]
    fn message_attachment_serializes_type_rename_and_optional_fields() {
        let att = MessageAttachment {
            attachment_id: "att_1".into(),
            attachment_type: AttachmentType::Image,
            file_name: "pic.png".into(),
            mime_type: None,
            size: None,
            sha256: None,
            url: None,
            expires_at: None,
        };
        let json = serde_json::to_string(&att).unwrap();
        assert!(
            json.contains("\"type\":\"image\""),
            "type must rename: {json}"
        );
        assert!(
            !json.contains("mime_type") && !json.contains("url") && !json.contains("expires_at"),
            "None optionals must be omitted: {json}"
        );
    }
}
