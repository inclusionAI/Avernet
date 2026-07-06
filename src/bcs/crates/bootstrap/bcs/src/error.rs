//! Error types for the Bot Coordination Service.

use thiserror::Error;
use bcs_service_api::ServiceError;

/// BCS error type.
#[derive(Debug, Error)]
pub enum BcsError {
    /// Session not found.
    #[error("Session not found: {0}")]
    SessionNotFound(String),

    /// Group not found.
    #[error("Group not found: {0}")]
    GroupNotFound(String),

    /// Bot not found in session.
    #[error("Bot '{0}' not found in session")]
    BotNotFound(String),

    /// Bot directory not found.
    #[error("Bot directory not found: {0}")]
    BotDirectoryNotFound(String),

    /// Failed to read bot context.
    #[error("Failed to read bot context for '{bot}': {source}")]
    BotContextReadError {
        bot: String,
        #[source]
        source: std::io::Error,
    },

    /// Fusion failed.
    #[error("Context fusion failed: {0}")]
    FusionFailed(String),

    /// Message routing failed.
    #[error("Failed to route message to bot '{bot}': {source}")]
    RoutingFailed {
        bot: String,
        #[source]
        source: anyhow::Error,
    },

    /// Router error (general).
    #[error("Router error: {0}")]
    RouterError(String),

    /// Invalid session configuration.
    #[error("Invalid session configuration: {0}")]
    InvalidConfig(String),

    /// Proposal not found or expired.
    #[error("Proposal not found or expired: {0}")]
    ProposalNotFound(String),

    /// Bot not registered.
    #[error("Bot '{0}' is not registered or has expired")]
    BotNotRegistered(String),

    /// Bot already has an active WebSocket connection.
    #[error("Bot '{0}' already has an active WebSocket connection")]
    BotAlreadyConnected(String),

    /// Invalid or expired session token.
    #[error("Invalid or expired session token")]
    InvalidSessionToken,

    /// WebSocket protocol error.
    #[error("WebSocket protocol error: {0}")]
    WsProtocolError(String),

    /// Invalid frame format.
    #[error("Invalid frame format: {0}")]
    InvalidFrameFormat(String),

    /// Bot not connected via WebSocket.
    #[error("Bot '{0}' is not connected via WebSocket")]
    BotNotConnected(String),

    /// Service error.
    #[error("Service error: {0}")]
    ServiceError(#[from] ServiceError),

    /// JSON error.
    #[error("JSON error: {0}")]
    JsonError(#[from] serde_json::Error),

    /// IO error.
    #[error("IO error: {0}")]
    IoError(#[from] std::io::Error),

    /// Provider error.
    #[error("Provider error: {0}")]
    ProviderError(String),

    /// Group pool service error.
    #[error("Group pool service error: {0}")]
    GroupPoolError(String),

    /// DingTalk API error.
    #[error("DingTalk API error: {0}")]
    DingTalkError(String),

    /// Bot user mapping missing.
    #[error("Bot user mapping missing: {0}")]
    BotUserMappingMissing(String),

    /// HTTP request error.
    #[error("HTTP request error: {0}")]
    HttpRequestError(#[from] reqwest::Error),
    /// Invalid operation.
    #[error("Invalid operation: {0}")]
    InvalidOperation(String),

    /// HTTP error.
    #[error("HTTP error: {0}")]
    HttpError(String),

    /// Storage initialization error.
    #[error("Storage initialization error: {0}")]
    StorageInitError(String),

    /// Conflict error (e.g., already exists).
    #[error("Conflict: {0}")]
    Conflict(String),

    /// Bot has too many active groups.
    #[error("{0}")]
    TooManyGroups(String),

    /// Group has too many members.
    #[error("{0}")]
    TooManyMembers(String),

    /// Group has too many messages.
    #[error("{0}")]
    TooManyMessages(String),

    /// Forbidden (ownership verification failed).
    #[error("Forbidden: {0}")]
    Forbidden(String),

    /// Unauthorized (authentication required).
    #[error("Unauthorized: {0}")]
    Unauthorized(String),

    /// Invalid request.
    #[error("Invalid request: {0}")]
    InvalidRequest(String),

    /// Protected bot requires friendship.
    #[error("Bot '{bot}' is protected and not a friend of '{driver}'")]
    NotFriends {
        bot: String,
        driver: String,
    },

    /// Bot is in private mode and cannot participate in collaboration network.
    #[error("Bot '{0}' is in private mode and cannot participate in collaboration network")]
    BotPrivate(String),

    /// Internal error.
    #[error("Internal error: {0}")]
    InternalError(String),
}

impl BcsError {
    pub fn variant_name(&self) -> &'static str {
        match self {
            Self::SessionNotFound(_) => "SessionNotFound",
            Self::GroupNotFound(_) => "GroupNotFound",
            Self::BotNotFound(_) => "BotNotFound",
            Self::BotDirectoryNotFound(_) => "BotDirectoryNotFound",
            Self::BotContextReadError { .. } => "BotContextReadError",
            Self::FusionFailed(_) => "FusionFailed",
            Self::RoutingFailed { .. } => "RoutingFailed",
            Self::RouterError(_) => "RouterError",
            Self::InvalidConfig(_) => "InvalidConfig",
            Self::ProposalNotFound(_) => "ProposalNotFound",
            Self::BotNotRegistered(_) => "BotNotRegistered",
            Self::BotAlreadyConnected(_) => "BotAlreadyConnected",
            Self::InvalidSessionToken => "InvalidSessionToken",
            Self::WsProtocolError(_) => "WsProtocolError",
            Self::InvalidFrameFormat(_) => "InvalidFrameFormat",
            Self::BotNotConnected(_) => "BotNotConnected",
            Self::ServiceError(_) => "ServiceError",
            Self::JsonError(_) => "JsonError",
            Self::IoError(_) => "IoError",
            Self::ProviderError(_) => "ProviderError",
            Self::GroupPoolError(_) => "GroupPoolError",
            Self::DingTalkError(_) => "DingTalkError",
            Self::BotUserMappingMissing(_) => "BotUserMappingMissing",
            Self::HttpRequestError(_) => "HttpRequestError",
            Self::InvalidOperation(_) => "InvalidOperation",
            Self::HttpError(_) => "HttpError",
            Self::StorageInitError(_) => "StorageInitError",
            Self::Conflict(_) => "Conflict",
            Self::TooManyGroups(_) => "TooManyGroups",
            Self::TooManyMembers(_) => "TooManyMembers",
            Self::TooManyMessages(_) => "TooManyMessages",
            Self::Forbidden(_) => "Forbidden",
            Self::Unauthorized(_) => "Unauthorized",
            Self::InvalidRequest(_) => "InvalidRequest",
            Self::NotFriends { .. } => "NotFriends",
            Self::BotPrivate(_) => "BotPrivate",
            Self::InternalError(_) => "InternalError",
        }
    }
}

/// Result type alias for BCS errors.
pub type Result<T> = std::result::Result<T, BcsError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_session_not_found_display() {
        let err = BcsError::SessionNotFound("session-123".to_string());
        assert_eq!(err.to_string(), "Session not found: session-123");
    }

    #[test]
    fn test_bot_not_found_display() {
        let err = BcsError::BotNotFound("bot-xyz".to_string());
        assert_eq!(err.to_string(), "Bot 'bot-xyz' not found in session");
    }

    #[test]
    fn test_bot_not_registered_display() {
        let err = BcsError::BotNotRegistered("unregistered-bot".to_string());
        assert_eq!(err.to_string(), "Bot 'unregistered-bot' is not registered or has expired");
    }

    #[test]
    fn test_proposal_not_found_display() {
        let err = BcsError::ProposalNotFound("token-abc".to_string());
        assert_eq!(err.to_string(), "Proposal not found or expired: token-abc");
    }

    #[test]
    fn test_router_error_display() {
        let err = BcsError::RouterError("Connection failed".to_string());
        assert_eq!(err.to_string(), "Router error: Connection failed");
    }

    #[test]
    fn test_fusion_failed_display() {
        let err = BcsError::FusionFailed("No context provided".to_string());
        assert_eq!(err.to_string(), "Context fusion failed: No context provided");
    }

    #[test]
    fn test_invalid_config_display() {
        let err = BcsError::InvalidConfig("Missing required field".to_string());
        assert_eq!(err.to_string(), "Invalid session configuration: Missing required field");
    }

    #[test]
    fn test_json_error_from() {
        let json_err = serde_json::from_str::<serde_json::Value>("invalid json").unwrap_err();
        let bcs_err: BcsError = json_err.into();

        match bcs_err {
            BcsError::JsonError(_) => (),
            _ => panic!("Expected JsonError variant"),
        }
    }

    #[test]
    fn test_io_error_from() {
        let io_err = std::io::Error::new(std::io::ErrorKind::NotFound, "file not found");
        let bcs_err: BcsError = io_err.into();

        match bcs_err {
            BcsError::IoError(_) => (),
            _ => panic!("Expected IoError variant"),
        }
    }

    #[test]
    fn test_ws_error_display() {
        let err = BcsError::WsProtocolError("Invalid frame".to_string());
        assert_eq!(err.to_string(), "WebSocket protocol error: Invalid frame");
    }

    #[test]
    fn test_bot_already_connected_display() {
        let err = BcsError::BotAlreadyConnected("bot-1".to_string());
        assert_eq!(err.to_string(), "Bot 'bot-1' already has an active WebSocket connection");
    }

    #[test]
    fn test_bot_not_connected_display() {
        let err = BcsError::BotNotConnected("bot-2".to_string());
        assert_eq!(err.to_string(), "Bot 'bot-2' is not connected via WebSocket");
    }

    #[test]
    fn test_result_type() {
        fn returns_result() -> Result<String> {
            Ok("success".to_string())
        }

        let result = returns_result();
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "success");
    }

    #[test]
    fn test_result_err() {
        fn returns_err() -> Result<String> {
            Err(BcsError::SessionNotFound("test".to_string()))
        }

        let result = returns_err();
        assert!(result.is_err());
    }
}