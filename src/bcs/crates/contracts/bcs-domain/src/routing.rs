//! Routing decision / target pure domain types.

use serde::{Deserialize, Serialize};

use crate::actor::{ActorKind, ActorStatus};
use crate::group::ParticipantMode;
use crate::message::DeliveryType;

/// Routing target for message delivery.
#[derive(Debug, Clone)]
pub struct RoutingTarget {
    /// Bot UUID (unique identifier assigned by BCS).
    pub bot_uuid: String,
    /// Bot URL.
    pub url: String,
    /// Whether this bot is the driver.
    pub is_driver: bool,
    /// How to deliver the message to this bot.
    pub delivery_type: DeliveryType,
}

/// Info about a @mention that was suppressed because the target bot is hidden.
#[derive(Debug, Clone)]
pub struct HiddenMentionInfo {
    pub hidden_bot_id: String,
    pub hidden_bot_name: String,
}

/// Routing decision.
#[derive(Debug, Clone)]
pub struct RoutingDecision {
    /// Targets to route to (always all participants - broadcast model).
    pub targets: Vec<RoutingTarget>,
    /// Extracted @mentions from the message.
    pub mentions: Vec<String>,
    /// Message with @mentions stripped (e.g. "@张三" becomes "张三").
    pub cleaned_message: String,
    /// @mentions that were suppressed because the target bot is hidden.
    pub hidden_mentions: Vec<HiddenMentionInfo>,
}

/// Result from sending a message to a bot.
#[derive(Debug, Clone)]
pub struct BotSendResult {
    /// Bot UUID that responded.
    pub bot_uuid: String,
    /// Response content.
    pub content: String,
    /// Whether the send was successful.
    pub success: bool,
    /// Error message if failed.
    pub error: Option<String>,
}

/// Result of route_and_send operation.
#[derive(Debug, Clone)]
pub struct RouteAndSendResult {
    /// All send results.
    pub results: Vec<BotSendResult>,
    /// Extracted @mentions from the message.
    pub mentions: Vec<String>,
}

/// Resolved per-participant overlay used by `route_with_overlay` (X.3).
///
/// This struct is owned by `bcs-domain` so it can be referenced from both
/// `bcs-service-api::core` traits and `plugin-api/store/*` contracts
/// (Phase 9-10), letting crates outside the routing crate pass it through the
/// `RoutingCoreService` trait without depending on a concrete router
/// implementation. The actual routing logic that consumes this overlay lives in
/// `bcs_routing::MessageRouter::route_with_overlay`.
#[derive(Debug, Clone)]
pub struct RouteParticipantOverlay {
    pub bot_uuid: String,
    pub bot_name: Option<String>,
    pub actor_kind: ActorKind,
    pub mode: Option<ParticipantMode>,
    pub status: ActorStatus,
    pub is_driver: bool,
}

/// Core representation of a structured route selector.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RouteSelectorWire {
    #[serde(rename = "type")]
    pub selector_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<String>,
}

/// Response mode for structured routing.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ResponseMode {
    Required,
    Optional,
}

impl Default for ResponseMode {
    fn default() -> Self {
        Self::Required
    }
}

/// Structured routing metadata after crossing the protocol boundary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatEventRouting {
    pub responders: Vec<RouteSelectorWire>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<ResponseMode>,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include_self: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dedupe_key: Option<String>,
}
