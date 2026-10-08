use async_trait::async_trait;

use super::{BotRegistryCoreService, Group, GroupKind};

pub use bcs_domain::{
    BotSendResult, ChatEventRouting, HiddenMentionInfo, ResponseMode, RouteAndSendResult,
    RouteParticipantOverlay, RouteSelectorWire, RoutingDecision, RoutingTarget,
};

/// Error from structured routing resolution.
#[derive(Debug, Clone, thiserror::Error)]
pub enum StructuredRoutingError {
    /// Target bot specified by UUID is not a participant of the group.
    #[error("target bot not in group: {0}")]
    TargetNotInGroup(String),
    /// Name selector matched multiple participants.
    #[error("ambiguous target name: {0}")]
    AmbiguousTarget(String),
    /// No selector matched any participant.
    #[error("no target matched")]
    NoTargetMatched,
    /// Invalid or unsupported selector.
    #[error("invalid selector: {0}")]
    InvalidSelector(String),
}

/// Service for message routing.
#[async_trait]
pub trait RoutingCoreService: Send + Sync {
    /// Determine routing for a message.
    ///
    /// # Arguments
    /// * `group` - The group
    /// * `message` - The message content
    /// * `sender_bot_id` - The bot_id of the sender (None for user messages)
    ///
    /// # Returns
    /// RoutingDecision with targets and mentions. Each target has a delivery_type:
    /// - DeliveryType::Send: Bot should respond (mentioned or coordinator for non-@)
    /// - DeliveryType::Inject: Bot should observe silently
    async fn route(
        &self,
        group: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
    ) -> RoutingDecision;

    /// Task X.3: route a message with a pre-resolved per-participant
    /// overlay (mode + status + is_driver). When the overlay slice is
    /// provided, implementations SHALL apply the Requirement 3.7 / 3.8 /
    /// 3.16 / 3.18 layered rules:
    ///
    /// - `mode=muted` (Bot) → forced Inject (still observe, never auto-reply)
    /// - `status=hidden` → forced Inject (transcript reaches actor, but
    ///    no visible response is provoked; X.7 layers `silent=true` on
    ///    the WS event for receivers)
    /// - `mode=absent` (Human) → @-mention dropped entirely; the absent
    ///    Human is treated as not-in-the-room for routing purposes
    /// - `mode=present` (Human) + @-mention → Send (regular notify)
    /// - any sender mode/status combination is the caller's
    ///   responsibility to gate at WS ingress (X.1/X.2 already do this)
    ///
    /// The default impl forwards to `route()` so existing implementations
    /// (NoopRoutingCoreService etc.) keep compiling unchanged. Production
    /// implementations MUST override to honor the overlay.
    async fn route_with_overlay(
        &self,
        group: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
        overlay: &[RouteParticipantOverlay],
    ) -> RoutingDecision {
        let _ = overlay; // legacy path: ignore overlay
        self.route(group, message, sender_bot_id).await
    }

    /// Route a direct-message group using "the other actor" semantics.
    ///
    /// `RoutingDecision.targets` remains Bot-only. If the other DM participant
    /// is Human, implementations return no Bot targets and frontend delivery
    /// is handled by message flow. The default fails closed for `Dm` groups so
    /// lightweight mocks do not accidentally apply normal-group routing.
    async fn route_dm_with_overlay(
        &self,
        group: &Group,
        message: &str,
        sender_actor_id: &str,
        overlay: &[RouteParticipantOverlay],
    ) -> RoutingDecision {
        if group.group_kind == GroupKind::Dm {
            let _ = (sender_actor_id, overlay);
            return RoutingDecision {
                targets: Vec::new(),
                mentions: Vec::new(),
                cleaned_message: message.to_string(),
                hidden_mentions: vec![],
            };
        }

        self.route_with_overlay(group, message, Some(sender_actor_id), overlay)
            .await
    }

    /// Send a message to a bot.
    async fn send_to_bot(
        &self,
        target: &RoutingTarget,
        message: &str,
        from: Option<&str>,
        group_id: Option<&str>,
    ) -> BotSendResult;

    /// Route and send a message, returning responses.
    async fn route_and_send(
        &self,
        group: &Group,
        message: &str,
        from: Option<&str>,
    ) -> RouteAndSendResult;

    /// Determine routing from structured routing metadata (ChatEventRouting).
    ///
    /// Resolves route selectors against group participants and bot registry,
    /// returning a RoutingDecision where resolved bots get DeliveryType::Send
    /// and remaining participants get DeliveryType::Inject.
    ///
    /// # Arguments
    /// * `group` - The group session
    /// * `routing` - Structured routing metadata from the final event
    /// * `sender_bot_id` - The bot that sent the final event
    /// * `registry` - Bot registry for capability lookups
    async fn route_structured(
        &self,
        group: &Group,
        routing: &ChatEventRouting,
        sender_bot_id: &str,
        registry: &dyn BotRegistryCoreService,
    ) -> Result<RoutingDecision, StructuredRoutingError> {
        let _ = (group, routing, sender_bot_id, registry);
        Err(StructuredRoutingError::NoTargetMatched)
    }
}
