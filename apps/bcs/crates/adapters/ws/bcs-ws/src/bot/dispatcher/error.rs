//! WebSocket dispatch error type, error mapping, and response-frame writers.

use bcs_protocol::{BcsFrame, ErrorShape, ResponseFrame};
use bcs_service_api::{ConnectError, BotUseCaseError, ServiceError, SessionUseCaseError};
use serde_json::Value;
use tokio::sync::mpsc;

pub type Result<T> = std::result::Result<T, BotWsDispatchError>;

#[derive(Debug, thiserror::Error)]
pub enum BotWsDispatchError {
    #[error("Invalid frame format: {0}")]
    InvalidFrameFormat(String),
    #[error("Bot '{0}' already has an active WebSocket connection")]
    BotAlreadyConnected(String),
    #[error("Conflict: {0}")]
    Conflict(String),
    #[error("Invalid or expired session token")]
    InvalidSessionToken,
    #[error("WebSocket protocol error: {0}")]
    WsProtocolError(String),
    #[error("JSON error: {0}")]
    JsonError(#[from] serde_json::Error),
    #[error("Service error: {0}")]
    ServiceError(#[from] ServiceError),
    #[error("bot.connect failed: {source}")]
    BotConnectError {
        bot_uuid: Option<String>,
        source: Box<BotWsDispatchError>,
    },
}

/// Map a SessionUseCaseError to a WS error code and message.
pub(super) fn map_session_error(err: SessionUseCaseError) -> (String, String) {
    match err {
        SessionUseCaseError::NotFound(s) => ("session_not_found".to_string(), s),
        SessionUseCaseError::InvalidParams(s) => ("invalid_params".to_string(), s),
        SessionUseCaseError::Conflict(s) => ("conflict".to_string(), s),
        SessionUseCaseError::Internal(e) => ("internal_error".to_string(), e.to_string()),
    }
}

pub(super) fn map_bot_use_case_error(err: BotUseCaseError) -> BotWsDispatchError {
    match err {
        BotUseCaseError::Connect(ConnectError::AlreadyConnected(id)) => {
            BotWsDispatchError::BotAlreadyConnected(id)
        }
        BotUseCaseError::Connect(ConnectError::AlreadyRegistered(id)) => {
            BotWsDispatchError::Conflict(format!("Bot '{}' is already registered", id))
        }
        BotUseCaseError::Connect(ConnectError::InvalidBotId) => {
            BotWsDispatchError::InvalidFrameFormat("bot_id cannot be empty".to_string())
        }
        BotUseCaseError::Connect(ConnectError::InvalidToken) => {
            BotWsDispatchError::InvalidSessionToken
        }
        BotUseCaseError::Connect(ConnectError::InternalError(msg)) => {
            BotWsDispatchError::WsProtocolError(msg)
        }
        BotUseCaseError::InvalidBotId(message) => BotWsDispatchError::InvalidFrameFormat(message),
        BotUseCaseError::Service(service_error) => BotWsDispatchError::ServiceError(service_error),
        other => BotWsDispatchError::WsProtocolError(other.to_string()),
    }
}

/// Send a success response frame.
pub(super) async fn send_ok(tx: &mpsc::Sender<String>, req_id: &str, payload: Value) -> Result<()> {
    let response = ResponseFrame::ok(req_id, payload);
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(json).await.map_err(|e| {
        BotWsDispatchError::WsProtocolError(format!("Failed to send response: {}", e))
    })?;
    Ok(())
}

/// Send an error response frame.
pub(super) async fn send_error(
    tx: &mpsc::Sender<String>,
    req_id: &str,
    code: &str,
    message: &str,
) -> Result<()> {
    let response = ResponseFrame::err(
        req_id,
        ErrorShape {
            code: code.to_string(),
            message: message.to_string(),
            details: None,
            retryable: false,
            retry_after_ms: None,
        },
    );
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(json).await.map_err(|e| {
        BotWsDispatchError::WsProtocolError(format!("Failed to send error response: {}", e))
    })?;
    Ok(())
}

pub(super) async fn send_service_error(
    tx: &mpsc::Sender<String>,
    req_id: &str,
    err: ServiceError,
) -> Result<()> {
    let (code, message) = match err {
        ServiceError::GroupNotFound(group_id) => {
            ("group_not_found", format!("Group not found: {group_id}"))
        }
        ServiceError::BotNotFound(bot_id) => (
            "target_not_found",
            format!("Target bot not found in group: {bot_id}"),
        ),
        ServiceError::Unauthorized(message) => {
            let code = if message.contains("driver bot") || message.contains("driver") {
                "not_driver"
            } else {
                "unauthorized"
            };
            (code, message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("service_mode=master_slave") =>
        {
            ("unsupported_service_mode", message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("invalid task complete status") =>
        {
            ("invalid_status", message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("target bot is not connected") =>
        {
            ("bot_not_connected", message)
        }
        ServiceError::InvalidOperation { message, .. }
            if message.contains("target bot is muted") =>
        {
            ("target_bot_muted", message)
        }
        other => ("internal_error", other.to_string()),
    };
    send_error(tx, req_id, code, &message).await
}
