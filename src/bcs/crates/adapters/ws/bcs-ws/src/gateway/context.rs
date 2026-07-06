//! Gateway context traits for decoupling from BCS server state.
//!
//! These traits allow the gateway module to be used independently
//! while still integrating with BCS when available.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::abort_manager::ChatAbortManager;
use super::event_broadcaster::EventBroadcaster;

// Note: BotConnectorType has been removed - bot communication is via WebSocket only

/// Gateway session information needed for chat operations.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GatewaySession {
    /// Session ID.
    pub id: String,

    /// Driver bot ID.
    pub driver_bot: String,

    /// List of participant bot IDs.
    pub participants: Vec<String>,

    /// Messages in the session.
    pub messages: Vec<serde_json::Value>,
}

/// Delivery type for a routing target.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
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

/// Routing target for message delivery.
#[derive(Debug, Clone)]
pub struct RoutingTarget {
    /// Bot UUID (unique identifier assigned by BCS).
    pub bot_uuid: String,

    /// Bot URL for HTTP requests.
    pub url: String,

    /// How to deliver the message to this bot.
    pub delivery_type: DeliveryType,
}

/// Result of routing decision.
#[derive(Debug, Clone)]
pub struct RoutingDecision {
    /// Targets to route to (always all participants - broadcast model).
    pub targets: Vec<RoutingTarget>,

    /// Extracted @mentions from the message.
    pub mentions: Vec<String>,

    /// Message with @mentions stripped (e.g. "@张三" becomes "张三").
    pub cleaned_message: String,
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

/// Trait for session access operations.
#[async_trait]
pub trait SessionAccess: Send + Sync {
    /// Get a session by key.
    async fn get(&self, session_key: &str) -> Option<GatewaySession>;
}

/// Trait for message routing operations.
#[async_trait]
pub trait MessageRouting: Send + Sync {
    /// Route a message to appropriate targets.
    ///
    /// # Arguments
    /// * `session_key` - The session identifier
    /// * `message` - The message content
    /// * `sender_bot_id` - The bot_id of the sender (None for user messages)
    async fn route(
        &self,
        session_key: &str,
        message: &str,
        sender_bot_id: Option<&str>,
    ) -> RoutingDecision;

    /// Route a message and send to targets, returning responses.
    /// This combines routing decision with actual bot communication.
    async fn route_and_send(
        &self,
        session_key: &str,
        message: &str,
        from: Option<&str>,
    ) -> RouteAndSendResult;
}

/// Trait for authentication validation.
#[async_trait]
pub trait AuthValidator: Send + Sync {
    /// Validate an authentication token.
    fn validate(&self, token: &str) -> bool;
}

/// Combined context for gateway operations.
pub struct GatewayContext {
    /// Abort manager for chat runs.
    pub abort_manager: ChatAbortManager,

    /// Event broadcaster for WebSocket clients.
    pub event_broadcaster: EventBroadcaster,
}

impl GatewayContext {
    /// Create a new gateway context.
    pub fn new() -> Self {
        Self {
            abort_manager: ChatAbortManager::new(),
            event_broadcaster: EventBroadcaster::default(),
        }
    }
}

impl Default for GatewayContext {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_gateway_session_serde() {
        let session = GatewaySession {
            id: "session-123".to_string(),
            driver_bot: "driver-bot".to_string(),
            participants: vec!["bot1".to_string(), "bot2".to_string()],
            messages: vec![
                serde_json::json!({
                    "id": "msg-1",
                    "role": "user",
                    "content": "Hello"
                }),
            ],
        };

        let json = serde_json::to_string(&session).unwrap();
        let parsed: GatewaySession = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.id, "session-123");
        assert_eq!(parsed.driver_bot, "driver-bot");
        assert_eq!(parsed.participants.len(), 2);
        assert_eq!(parsed.messages.len(), 1);
    }

    #[test]
    fn test_gateway_session_empty_messages() {
        let session = GatewaySession {
            id: "session-empty".to_string(),
            driver_bot: "driver".to_string(),
            participants: vec![],
            messages: vec![],
        };

        let json = serde_json::to_string(&session).unwrap();
        let parsed: GatewaySession = serde_json::from_str(&json).unwrap();

        assert!(parsed.participants.is_empty());
        assert!(parsed.messages.is_empty());
    }

    #[test]
    fn test_routing_target() {
        let target = RoutingTarget {
            bot_uuid: "bot1".to_string(),
            url: "http://localhost:20001".to_string(),
            delivery_type: DeliveryType::Send,
        };

        assert_eq!(target.bot_uuid, "bot1");
        assert_eq!(target.url, "http://localhost:20001");
        assert_eq!(target.delivery_type, DeliveryType::Send);
    }

    #[test]
    fn test_routing_decision() {
        let decision = RoutingDecision {
            targets: vec![
                RoutingTarget {
                    bot_uuid: "driver".to_string(),
                    url: "http://localhost:20001".to_string(),
                    delivery_type: DeliveryType::Send,
                },
                RoutingTarget {
                    bot_uuid: "consultant".to_string(),
                    url: "http://localhost:20002".to_string(),
                    delivery_type: DeliveryType::Inject,
                },
            ],
            mentions: vec!["consultant".to_string()],
            cleaned_message: String::new(),
        };

        assert_eq!(decision.targets.len(), 2);
        assert_eq!(decision.mentions, vec!["consultant"]);
    }

    #[test]
    fn test_routing_decision_empty() {
        let decision = RoutingDecision {
            targets: vec![],
            mentions: vec![],
            cleaned_message: String::new(),
        };

        assert!(decision.targets.is_empty());
        assert!(decision.mentions.is_empty());
    }

    #[test]
    fn test_bot_send_result_success() {
        let result = BotSendResult {
            bot_uuid: "bot1".to_string(),
            content: "Response content".to_string(),
            success: true,
            error: None,
        };

        assert!(result.success);
        assert!(result.error.is_none());
        assert_eq!(result.content, "Response content");
    }

    #[test]
    fn test_bot_send_result_failure() {
        let result = BotSendResult {
            bot_uuid: "bot2".to_string(),
            content: String::new(),
            success: false,
            error: Some("Connection failed".to_string()),
        };

        assert!(!result.success);
        assert!(result.error.is_some());
        assert_eq!(result.error.unwrap(), "Connection failed");
    }

    #[test]
    fn test_route_and_send_result() {
        let result = RouteAndSendResult {
            results: vec![
                BotSendResult {
                    bot_uuid: "bot1".to_string(),
                    content: "Response 1".to_string(),
                    success: true,
                    error: None,
                },
                BotSendResult {
                    bot_uuid: "bot2".to_string(),
                    content: "Response 2".to_string(),
                    success: true,
                    error: None,
                },
            ],
            mentions: vec!["bot1".to_string(), "bot2".to_string()],
        };

        assert_eq!(result.results.len(), 2);
        assert_eq!(result.mentions.len(), 2);
    }

    #[test]
    fn test_route_and_send_result_empty() {
        let result = RouteAndSendResult {
            results: vec![],
            mentions: vec![],
        };

        assert!(result.results.is_empty());
        assert!(result.mentions.is_empty());
    }

    #[test]
    fn test_gateway_context_new() {
        let _ctx = GatewayContext::new();
        // Should not panic
    }

    #[test]
    fn test_gateway_context_default() {
        let _ctx = GatewayContext::default();
        // Should not panic
    }
}