//! Channel / IM domain types.
//!
//! Pure data types only: no traits, no I/O.

use serde::{Deserialize, Serialize};

use crate::collaboration::HumanInputNotificationMode;

pub type ChannelType = String;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BindingTarget {
    Group { group_id: String },
    Bot { bot_id: String },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Visibility {
    FullTranscript,
    LeadOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BindingStatus {
    Active,
    Disabled,
}

pub type ChannelConfig = serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroupChatScope {
    ConversationShared,
    PerSender,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionScope {
    Conversation,
    PerSender,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChannelBinding {
    pub id: String,
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    pub env: String,
    pub status: BindingStatus,
    pub created_by: Option<String>,
    pub config: ChannelConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConversationSessionMap {
    pub binding_id: String,
    pub im_conversation_id: String,
    pub im_conversation_type: String,
    pub session_scope: SessionScope,
    pub im_user_id: Option<String>,
    pub bcs_session_id: String,
    pub last_active_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImParticipantMap {
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub im_user_id: String,
    pub actor_id: String,
    pub display_name: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HumanInputRequestStatus {
    Queued,
    Notifying,
    Active,
    Responded,
    Expired,
    Cancelled,
    DeliveryFailed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HumanInputRequest {
    pub request_id: String,
    pub session_id: String,
    pub run_id: String,
    pub node_id: String,
    pub binding_id: String,
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub notification_mode: HumanInputNotificationMode,
    pub reply_scope_key: String,
    pub active_slot_key: Option<String>,
    pub assignee_actor_id: String,
    pub im_conversation_id: String,
    pub im_conversation_type: String,
    pub im_user_id: Option<String>,
    pub node_display_name: String,
    pub notification_text: String,
    pub deadline_ms: u64,
    pub status: HumanInputRequestStatus,
    pub provider_message_ref: Option<String>,
    pub delivery_attempts: u32,
    pub last_delivery_error: Option<String>,
    pub created_at: u64,
    pub activated_at: Option<u64>,
    pub responded_at: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn channel_config_preserves_provider_json() -> Result<(), serde_json::Error> {
        let config: ChannelConfig = serde_json::json!({
            "robot_code": "provider_robot",
            "nested": {
                "secret": "secret_value"
            },
            "send_mode": {
                "mode": "streaming_card",
                "card_template_id": "card_tpl_123"
            }
        });

        let json = serde_json::to_value(&config)?;

        assert_eq!(json["robot_code"], "provider_robot");
        assert_eq!(json["nested"]["secret"], "secret_value");
        assert_eq!(json["send_mode"]["mode"], "streaming_card");
        assert_eq!(json["send_mode"]["card_template_id"], "card_tpl_123");

        let round_trip: ChannelConfig = serde_json::from_value(json)?;
        assert_eq!(round_trip["robot_code"], "provider_robot");
        assert_eq!(round_trip["nested"]["secret"], "secret_value");
        assert_eq!(round_trip["send_mode"]["card_template_id"], "card_tpl_123");

        Ok(())
    }

    #[test]
    fn binding_target_group_round_trips_through_json() -> Result<(), serde_json::Error> {
        let target = BindingTarget::Group {
            group_id: "group_123".to_string(),
        };

        let json = serde_json::to_value(&target)?;
        assert_eq!(json["group"]["group_id"], "group_123");

        let round_trip: BindingTarget = serde_json::from_value(json)?;
        assert_eq!(round_trip, target);

        Ok(())
    }
}
