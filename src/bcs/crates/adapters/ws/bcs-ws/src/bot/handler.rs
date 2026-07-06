//! WebSocket connection handler for BCS.
//!
//! Handles the lifecycle of a WebSocket connection from a bot.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use axum::extract::ws::{Message, WebSocket};
use bcs_service_api::{
    BotRuntimeDisconnectCommand, WsCloseReason, WsLifecycleInstrumentationHook,
    WsErrorKind, WsPeer,
};
use futures::{SinkExt, StreamExt};
use tokio::sync::mpsc;
use tracing::{debug, error, info, warn};

use crate::bot::{BotDispatchOutcome, BotDispatchState, BotWsDispatchError, dispatch_frame};

/// Handle a WebSocket connection from a bot.
///
/// This function manages the full lifecycle of a bot connection:
/// 1. Sets up read/write loops
/// 2. Dispatches incoming frames
/// 3. Handles cleanup on disconnect
pub async fn handle_connection(
    socket: WebSocket,
    state: Arc<BotDispatchState>,
    metrics_hook: Arc<dyn WsLifecycleInstrumentationHook>,
    agent_token: Option<String>,
    agent_code_header: Option<String>,
) {
    let (mut ws_tx, mut ws_rx) = socket.split();
    let (client_tx, mut client_rx) = mpsc::channel::<String>(256);

    let connected_at = Instant::now();
    let send_error_seen = Arc::new(AtomicBool::new(false));
    let mut close_reason = WsCloseReason::Unknown;
    metrics_hook
        .accepted(WsPeer::Bot, crate::bot::BOT_WS_ENDPOINT)
        .await;

    // Write loop: send frames from channel to WebSocket
    let write_send_error = send_error_seen.clone();
    let write_handle = tokio::spawn(async move {
        while let Some(msg) = client_rx.recv().await {
            let close_after_send = should_close_after_sending_bot_frame(&msg);
            if ws_tx.send(Message::Text(msg.into())).await.is_err() {
                debug!("WebSocket send error, connection likely closed");
                write_send_error.store(true, Ordering::Relaxed);
                break;
            }
            if close_after_send {
                break;
            }
        }
        // Send close message when done
        let _ = ws_tx.send(Message::Close(None)).await;
    });

    // Track registered bot_id for cleanup
    let mut registered_bot_id: Option<String> = None;

    info!(duration_ms = 0u64, "New WebSocket connection established");

    // Read loop: process incoming frames
    while let Some(msg_result) = ws_rx.next().await {
        match msg_result {
            Ok(Message::Text(text)) => {
                info!(len = text.len(), text = %text, "Received bot frame");

                match dispatch_frame(&state, &text, &client_tx, &mut registered_bot_id).await {
                    Ok(outcome) => {
                        debug!("Frame dispatched successfully");
                        if let BotDispatchOutcome::BotConnect { registered } = outcome {
                            if registered {
                                metrics_hook
                                    .registered(WsPeer::Bot, crate::bot::BOT_WS_ENDPOINT)
                                    .await;
                                if let (Some(token), Some(bot_uuid)) =
                                    (&agent_token, &registered_bot_id)
                                {
                                    if let Some(exp) = crate::bot::decode_jwt_exp(token) {
                                        state
                                            .bot_connections
                                            .set_token_expires_at(bot_uuid, exp)
                                            .await;
                                    }
                                }
                                if let (Some(backfill), Some(bot_uuid)) =
                                    (&state.agent_credential_backfill, &registered_bot_id)
                                {
                                    let backfill = Arc::clone(backfill);
                                    let bot_uuid = bot_uuid.clone();
                                    let at = agent_token.clone();
                                    let ac = agent_code_header.clone();
                                    tokio::spawn(async move {
                                        backfill.backfill(&bot_uuid, at, ac).await;
                                    });
                                }
                            } else {
                                metrics_hook
                                    .error(
                                        WsPeer::Bot,
                                        crate::bot::BOT_WS_ENDPOINT,
                                        WsErrorKind::RegisterRejected,
                                    )
                                    .await;
                            }
                        }
                    }
                    Err(e) => {
                        warn!(error = %e, "Frame dispatch error");
                        let error_kind = if matches!(&e, BotWsDispatchError::BotConnectError(_)) {
                            WsErrorKind::RegisterRejected
                        } else {
                            WsErrorKind::DispatchError
                        };
                        metrics_hook
                            .error(WsPeer::Bot, crate::bot::BOT_WS_ENDPOINT, error_kind)
                            .await;
                        // Send error response if possible
                        let _ = client_tx
                            .send(
                                serde_json::json!({
                                    "type": "res",
                                    "id": "error",
                                    "ok": false,
                                    "error": {
                                        "code": "dispatch_error",
                                        "message": e.to_string()
                                    }
                                })
                                .to_string(),
                            )
                            .await;
                    }
                }
            }
            Ok(Message::Binary(data)) => {
                warn!(len = data.len(), "Received unexpected binary frame");
            }
            Ok(Message::Ping(_data)) => {
                debug!("Received ping, sending pong");
                // WebSocket automatically responds to ping with pong
            }
            Ok(Message::Pong(_)) => {
                debug!("Received pong");
            }
            Ok(Message::Close(close_frame)) => {
                close_reason = WsCloseReason::ClientClose;
                info!(
                    close_code = ?close_frame.as_ref().map(|f| f.code),
                    close_reason = ?close_frame.as_ref().map(|f| f.reason.to_string()),
                    "WebSocket close frame received"
                );
                break;
            }
            Err(e) => {
                if is_connection_reset_without_closing_handshake(&e) {
                    close_reason = WsCloseReason::ClientClose;
                    debug!(error = %e, "WebSocket connection reset without close frame");
                } else {
                    close_reason = WsCloseReason::ProtocolError;
                    error!(error = %e, "WebSocket error");
                    metrics_hook
                        .error(
                            WsPeer::Bot,
                            crate::bot::BOT_WS_ENDPOINT,
                            WsErrorKind::ProtocolError,
                        )
                        .await;
                }
                break;
            }
        }
    }

    if send_error_seen.load(Ordering::Relaxed) {
        close_reason = WsCloseReason::SendError;
        metrics_hook
            .error(
                WsPeer::Bot,
                crate::bot::BOT_WS_ENDPOINT,
                WsErrorKind::SendError,
            )
            .await;
    }

    // Cleanup on disconnect
    if let Some(bot_id) = &registered_bot_id {
        // Remove streaming connection from registry and adapter sender map.
        if let Err(err) = state
            .bot_runtime
            .disconnect_streaming(BotRuntimeDisconnectCommand {
                bot_id: bot_id.clone(),
            })
            .await
        {
            warn!(bot_id = %bot_id, error = %err, "Failed to record bot streaming disconnect");
        }
        state.bot_connections.disconnect(bot_id).await;

        // Bot is now offline but token persists for reconnection

        info!(
            bot_id = %bot_id,
            duration_ms = connected_at.elapsed().as_millis() as u64,
            "Bot disconnected"
        );
    } else {
        info!(
            duration_ms = connected_at.elapsed().as_millis() as u64,
            "Unregistered WebSocket connection closed"
        );
    }

    // Stop the write loop
    write_handle.abort();
    metrics_hook
        .closed(
            WsPeer::Bot,
            crate::bot::BOT_WS_ENDPOINT,
            close_reason,
            connected_at.elapsed(),
        )
        .await;
}

fn should_close_after_sending_bot_frame(frame: &str) -> bool {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(frame) else {
        return false;
    };

    match value.get("type").and_then(|ty| ty.as_str()) {
        Some("event") => value
            .get("event")
            .and_then(|event| event.as_str())
            .is_some_and(|event| event == "bot.kicked"),
        Some("res") => {
            let is_error = value
                .get("ok")
                .and_then(|ok| ok.as_bool())
                .is_some_and(|ok| !ok);
            let is_provider_delivery_rejection = value
                .get("error")
                .and_then(|error| error.get("code"))
                .and_then(|code| code.as_str())
                .is_some_and(|code| code == "bot_delivery_is_provider");
            is_error && is_provider_delivery_rejection
        }
        _ => false,
    }
}

fn is_connection_reset_without_closing_handshake(error: &impl std::fmt::Display) -> bool {
    error
        .to_string()
        .contains("Connection reset without closing handshake")
}

#[cfg(test)]
mod tests {
    use super::{
        is_connection_reset_without_closing_handshake, should_close_after_sending_bot_frame,
    };

    #[test]
    fn closes_after_bot_kicked_event_frame() {
        let frame = r#"{"type":"event","event":"bot.kicked","payload":{"reason":"delivery_switched_to_provider"}}"#;
        assert!(should_close_after_sending_bot_frame(frame));
    }

    #[test]
    fn closes_after_provider_delivery_rejection_response() {
        let frame = r#"{"type":"res","id":"connect-1","ok":false,"error":{"code":"bot_delivery_is_provider","message":"Bot delivery is configured for HttpProvider"}}"#;
        assert!(should_close_after_sending_bot_frame(frame));
    }

    #[test]
    fn does_not_close_after_normal_response_frame() {
        let frame = r#"{"type":"res","id":"connect-1","ok":true,"payload":{}}"#;
        assert!(!should_close_after_sending_bot_frame(frame));
    }

    #[test]
    fn treats_connection_reset_without_close_frame_as_client_disconnect() {
        let error = "WebSocket protocol error: Connection reset without closing handshake";
        assert!(is_connection_reset_without_closing_handshake(&error));
    }
}
