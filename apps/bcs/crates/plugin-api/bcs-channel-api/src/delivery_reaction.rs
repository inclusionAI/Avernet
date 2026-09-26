//! Typed wire contract for delivery-status reactions sent to channel providers.

use serde::{Deserialize, Serialize};

/// Discriminator carried in `ChannelOutboundEvent::raw_payload`.
pub const DELIVERY_REACTION_EVENT_TYPE: &str = "message.delivery.reaction";

/// Provider-visible delivery state for the source IM message.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryReactionState {
    Queued,
    Processing,
    Expired,
    Clear,
}

impl DeliveryReactionState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Processing => "processing",
            Self::Expired => "expired",
            Self::Clear => "clear",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
enum DeliveryReactionEventType {
    #[serde(rename = "message.delivery.reaction")]
    MessageDeliveryReaction,
}

/// Payload for a `System` channel event that updates the reaction attached to
/// `ChannelOutboundEvent::source_im_message_id`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeliveryReactionEvent {
    #[serde(rename = "type")]
    event_type: DeliveryReactionEventType,
    pub state: DeliveryReactionState,
    /// Durable BCS message id for correlation. Providers anchor the reaction
    /// with `ChannelOutboundEvent::source_im_message_id`.
    pub message_id: String,
}

impl DeliveryReactionEvent {
    pub fn new(state: DeliveryReactionState, message_id: impl Into<String>) -> Self {
        Self {
            event_type: DeliveryReactionEventType::MessageDeliveryReaction,
            state,
            message_id: message_id.into(),
        }
    }
}
